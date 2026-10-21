import time

from cloudshell.shell.core.resource_driver_interface import ResourceDriverInterface
from cloudshell.shell.core.driver_context import InitCommandContext, ResourceCommandContext, AutoLoadResource, \
    AutoLoadAttribute, AutoLoadDetails, CancellationContext
from cloudshell.shell.core.session.logging_session import LoggingSessionContext
from cloudshell.shell.core.session.cloudshell_session import CloudShellSessionContext


from cloudshell.api.cloudshell_api import CloudShellAPISession

#from data_model import *  # run 'shellfoundry generate' to generate data model classes

FIELD_SEPARATOR = "|!"
POWER_ON_COMMAND = "PowerOn"
POWER_OFF_COMMAND = "PowerOff"
EXCLUDE_WAIT_TIMEOUT = 30  # seconds
EXCLUDE_POLL_INTERVAL = 1  # seconds


class AdminresourcehandlerDriver (ResourceDriverInterface):

    def __init__(self):
        """
        ctor must be without arguments, it is created with reflection at run time
        """
        pass

    def initialize(self, context):
        """
        Initialize the driver session, this function is called everytime a new instance of the driver is created
        This is a good place to load and cache the driver configuration, initiate sessions etc.
        :param InitCommandContext context: the context the command runs on
        """
        pass

    def cleanup(self):
        """
        Destroy the driver session, this function is called everytime a driver instance is destroyed
        This is a good place to close any open sessions, finish writing to log files
        """
        pass

    def AfterResourcesChanged(self, context, actionDetails, resourcesDetails, serviceDetails,
                              resourcesRemoved, resourcesAdded, modifiedResources):
        """
        Intervene after a resource is added, removed, or has an attribute changed
        :param ResourceCommandContext context: the context the command runs on
        :param str actionDetails: details about the action
        :param str resourcesDetails: details about the resource
        :param str serviceDetails: details about the service
        :param str resourcesRemoved: details about the added resources
        :param str resourcesAdded: details about the removed resources
        :param str modifiedResources: details about the modified resources
        """
        api = CloudShellSessionContext(context).get_api()
        logger = LoggingSessionContext.get_logger_with_thread_id(context)

        logger.info(f"AfterResourcesChanged called with actionDetails: {actionDetails}, resourcesDetails: {resourcesDetails}, serviceDetails: {serviceDetails}, resourcesRemoved: {resourcesRemoved}, resourcesAdded: {resourcesAdded}, modifiedResources: {modifiedResources}")
        # api.WriteMessageToReservationOutput(context.reservation.reservation_id, f"AfterResourcesChanged called with actionDetails: {actionDetails}, resourcesDetails: {resourcesDetails}, serviceDetails: {serviceDetails}, resourcesRemoved: {resourcesRemoved}, resourcesAdded: {resourcesAdded}, modifiedResources: {modifiedResources}")

        for resource in self._parse_resources(resourcesAdded):
            self._power_if_eligible(api, logger, context, resource, POWER_ON_COMMAND, api.PowerOnResource)

        # return "AfterResourcesChanged executed successfully"


    def BeforeResourcesChanged(self, context, actionDetails, resourcesDetails, serviceDetails,
                              resourcesRemoved, resourcesAdded, resourcesModified):
        """
        Intervene before a resource is added, removed, or has an attribute changed
        :param ResourceCommandContext context: the context the command runs on
        :param str actionDetails: details about the action
        :param str resourcesDetails: details about the resource
        :param str serviceDetails: details about the service
        :param str resourcesRemoved: details about the added resources
        :param str resourcesAdded: details about the removed resources
        :param str resourcesModified: details about the modified resources
        """
        api = CloudShellSessionContext(context).get_api()
        logger = LoggingSessionContext.get_logger_with_thread_id(context)

        logger.info(f"BeforeResourcesChanged called with actionDetails: {actionDetails}\n resourcesDetails: {resourcesDetails}\n serviceDetails: {serviceDetails}\n resourcesRemoved: {resourcesRemoved}\n resourcesAdded: {resourcesAdded}\n modifiedResources: {resourcesModified}")
        # api.WriteMessageToReservationOutput(context.reservation.reservation_id, f"BeforeResourcesChanged called with actionDetails: {actionDetails}\n resourcesDetails: {resourcesDetails}\n serviceDetails: {serviceDetails}\n resourcesRemoved: {resourcesRemoved}\n resourcesAdded: {resourcesAdded}\n modifiedResources: {resourcesModified}")

        for resource in self._parse_resources(resourcesRemoved):
            # the resource may already be gone from the reservation by the time power off fires,
            # so run it outside the reservation (admins may send a blank reservation id to this API),
            # which requires the resource to be excluded while the command runs
            self._power_if_eligible(api, logger, context, resource, POWER_OFF_COMMAND, api.PowerOffResource,
                                    reservation_id="", exclude_while_running=True)

        # return "BeforeResourcesChanged executed successfully"

    @staticmethod
    def _parse_resources(raw):
        """
        Parse a resources string from the admin extension hooks into a list of dicts.
        Format: first line is the header, then one line per resource, fields separated by '|!'
        e.g. 'Name|!FullName|!Family|!Model|!Address|!Shared\nSimple1|!Simple1|!Simple Resource|!Simple Model|!127.0.0.1|!False'
        :param str raw: the resourcesAdded / resourcesRemoved string
        :rtype: list[dict]
        """
        lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
        if len(lines) < 2:
            return []
        header = lines[0].split(FIELD_SEPARATOR)
        return [dict(zip(header, line.split(FIELD_SEPARATOR))) for line in lines[1:]]

    @staticmethod
    def _wait_until_excluded(api, logger, full_name):
        """
        The exclude can take a moment to take effect, so poll until the resource reports as excluded.
        Gives up after EXCLUDE_WAIT_TIMEOUT seconds and carries on, so the power command's own error surfaces.
        """
        start = time.time()
        while True:
            excluded = str(api.GetResourceDetails(full_name).Excluded).strip().lower() == "true"
            waited = time.time() - start
            if excluded:
                logger.info(f"'{full_name}' reported as excluded after {waited:.1f}s")
                return
            if waited >= EXCLUDE_WAIT_TIMEOUT:
                logger.warning(f"'{full_name}' still not reported as excluded after {waited:.1f}s, continuing anyway")
                return
            time.sleep(EXCLUDE_POLL_INTERVAL)

    def _power_if_eligible(self, api, logger, context, resource, command_name, power_method, reservation_id=None,
                           exclude_while_running=False):
        """
        Run the power method on the resource only if it is not a deployed app, is unshared,
        and has the given connected command. Errors are logged so one resource cannot break the others.
        :param CloudShellAPISession api: the API session
        :param ResourceCommandContext context: the context the command runs on
        :param dict resource: a parsed resource row
        :param str command_name: the connected command the resource must have, e.g. PowerOn
        :param power_method: api.PowerOnResource or api.PowerOffResource
        :param str reservation_id: reservation id to pass to the power method; defaults to the current
            reservation, pass "" to run outside any reservation (admins only)
        :param bool exclude_while_running: exclude the resource before running the power method and include it
            again afterwards (even if the power method fails), unless it was already excluded
        """
        output_reservation_id = context.reservation.reservation_id
        if reservation_id is None:
            reservation_id = output_reservation_id
        full_name = resource.get("FullName") or resource.get("Name")
        try:
            if str(resource.get("Shared", "")).strip().lower() == "true":
                logger.info(f"Skipping {command_name} on '{full_name}': resource is shared")
                return

            details = api.GetResourceDetails(full_name)
            if hasattr(details.VmDetails, "UID"):
                logger.info(f"Skipping {command_name} on '{full_name}': resource is a deployed app")
                return

            connected_commands = api.GetResourceConnectedCommands(full_name).Commands
            if command_name not in [command.Name for command in connected_commands]:
                logger.info(f"Skipping {command_name} on '{full_name}': no '{command_name}' connected command")
                return

            # running a power command outside a reservation requires the resource to be excluded;
            # only re-include it if we were the ones who excluded it
            excluded_by_us = False
            if exclude_while_running and str(details.Excluded).strip().lower() != "true":
                logger.info(f"Excluding '{full_name}' before {power_method.__name__}")
                api.ExcludeResource(full_name)
                excluded_by_us = True
                self._wait_until_excluded(api, logger, full_name)
            try:
                logger.info(f"Running {power_method.__name__} on '{full_name}'")
                power_method(reservation_id, full_name)
                api.WriteMessageToReservationOutput(output_reservation_id, f"{power_method.__name__} executed on '{full_name}'")
            finally:
                if excluded_by_us:
                    logger.info(f"Including '{full_name}' after {power_method.__name__}")
                    api.IncludeResource(full_name)
        except Exception as e:
            logger.exception(f"{command_name} on '{full_name}' failed: {e}")
            api.WriteMessageToReservationOutput(output_reservation_id, f"{command_name} on '{full_name}' failed: {e}")

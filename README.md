# AdminService-AutoPower
An Admin Only custom service shell with a reference implementation of CloudShell Service Extensibility

The shell automatically powers on resources when they are added to a sandbox and powers them off when they are removed, without any action from the sandbox user. It is built on the `AdminOnlyCustomService` model, which lets an administrator hook into the `BeforeResourcesChanged` and `AfterResourcesChanged` events that CloudShell raises when resources are added to or removed from a live sandbox. It can be used as a reference for implementing other logic that reacts to those events.

## How it works

CloudShell raises two events when resources in an active sandbox change, and the service driver implements a command for each of them.

| Event | Resources examined | Action |
| --- | --- | --- |
| After Resources Changed | Resources that were added | `PowerOnResource` |
| Before Resources Changed | Resources that were removed | `PowerOffResource` |

A resource must meet all of the following conditions before it is powered on or off. A resource that fails any condition is skipped, and the reason is written to the driver log.

### Power-on flow detail

    The resource must not be shared
    The resource must not be a deployed app (its resource details contain no VmDetails UID)
    There must be a 'PowerOn' connected command available to the resource
    PowerOnResource is called in the context of the sandbox

### Power-off flow detail

    The resource must not be shared
    The resource must not be a deployed app (its resource details contain no VmDetails UID)
    There must be a 'PowerOff' connected command available to the resource
    The resource is excluded, unless it already is, and the service waits (up to 30 seconds) for the exclude to take effect
    PowerOffResource is called with a blank reservation ID
    The resource is included again, unless it was already excluded before the flow started

The power-off event is raised before the resource leaves the sandbox, but the resource is already out of the reservation by the time the power command runs. For that reason the command is run outside of the reservation, which administrators are permitted to do with this API. Running a power command outside of a reservation requires the resource to be excluded, which is why the resource is excluded for the duration of the command and then included again.

Whether a resource is shared is taken from the `Shared` value that CloudShell provides for each added or removed resource. A resource that is connected to a power port on a PDU resource exposes the `PowerOn` and `PowerOff` connected commands.

The outcome of each power command, including any error, is written to the sandbox's output window. Detailed decisions, including the reason a resource was skipped, are written to the driver log.

## Installation instructions

### Components
Each release provides two files:
* AdminAutoPowerResource.zip - the shell package
* offlinedependencies.zip - the Python packages that the shell driver requires, for CloudShell servers that cannot reach the internet. This file is not needed when the server can download packages from PyPI.

### Shell package
1. Download AdminAutoPowerResource.zip from the Releases page. Alternatively, package the shell from the root of the repository using [shellfoundry](https://github.com/QualiSystems/shellfoundry): `shellfoundry pack`. The package is created in the `dist` folder.
1. Log into the CloudShell web UI as an administrator.
1. Navigate to 'Manage'->'Shells' and drag the package into the browser window.
1. Add the service to every blueprint in which resources should be powered automatically.
1. Add the service to every blueprint template that should apply to, which is likely to include the system default blueprint template named 'CloudShell Sandbox Template'.

The service activates automatically whenever it is present in a sandbox, so no further configuration is required. By default, a new blueprint is based on the blueprint template of its domain when the domain contains exactly one template, which is the default state. Adding the service to that template therefore applies it to all blueprints created afterwards. Existing blueprints must be updated individually.

The shell derives from `cloudshell.nodes.AdminOnlyCustomService` and is placed in the `Admin-Services` service category.

### Service extension DLLs
Admin only custom services depend on the CloudShell service extension DLLs `GenericServiceExtension.dll` and `ServiceExtension.dll`, which are located in the `QualiSystems\CloudShell\Server\Extensions` folder of the Quali Server installation. They are copied there from the CloudShell installation source, under `CloudShell\Integration\Extensions`. If the installation source was downloaded or copied from another machine, Windows may mark these files as blocked, which prevents them from loading. See Common Errors below for the symptom and the fix.

### Offline dependencies
The driver runs on Python 3 and requires the packages listed in `src/requirements.txt` and their dependencies. When the CloudShell server has no internet access, install these packages into the PyPi Server repository of the Quali Server before importing the shell.

1. Extract offlinedependencies.zip to a temporary folder on the Quali Server. It contains the package files (wheels) in a flat layout.
1. Copy the package files into the PyPi Server repository folder. The default location is `C:\Program Files (x86)\QualiSystems\CloudShell\Server\Config\Pypi Server Repository`. Do not extract the individual wheel files.
1. Import the shell package as described above.

The packages are `cloudshell-shell-core` 5.1.1, `cloudshell-automation-api` 2026.2.0.190036, `cloudshell-logging` 1.0.1, `jsonpickle` 4.1.4 and `urllib3` 2.8.0. All of them are pure Python wheels, and the set was verified to install offline on Python 3.13. Packages in the repository folder are shared by all scripts and drivers on the server, so check that these versions do not conflict with packages that are already present.

#### Rebuilding offlinedependencies.zip
`shellfoundry dist` downloads packages for the operating system and Python version of the machine that runs it, so it is not suitable for preparing a Windows, Python 3.13 package on another platform. Use the following steps instead. `cloudshell-automation-api` is published only as a source distribution, so it is first built into a wheel, which is then supplied to the download.

    python3.13 -m pip wheel cloudshell-automation-api --no-deps -w wheelhouse
    python3.13 -m pip download -r src/requirements.txt -d offline --find-links wheelhouse --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313 --only-binary=:all:
    cd offline && zip -j ../dist/offlinedependencies.zip *.whl

Verify the result by installing it into a clean Python 3.13 virtual environment: `pip install --no-index --find-links offline -r src/requirements.txt`.

## Usage

Once the service is present in a sandbox, adding a resource to the sandbox or removing one runs the matching power command automatically. Resources must be connected to a power port on a PDU resource for the `PowerOn` and `PowerOff` connected commands to be available.

## Debugging

The driver writes to the standard CloudShell driver log under the `QS.AdminResourceHandler` logger. Each execution begins with an execution info block showing the reservation ID, user and host, followed by the details of the event.

The following messages in the log explain why a resource was not powered on or off:

* `Skipping PowerOn on '<resource>': resource is shared`
* `Skipping PowerOn on '<resource>': resource is a deployed app`
* `Skipping PowerOn on '<resource>': no 'PowerOn' connected command`

The same messages apply to `PowerOff` for removed resources.

### Common Errors
* `System.IO.FileLoadException: Could not load file or assembly 'GenericServiceExtension, Version=2.0.0.0 ...' or one of its dependencies. Operation is not supported. (Exception from HRESULT: 0x80131515)`

    This error is logged by `QsExtensionHost` on the Quali Server, and the service does not run. It occurs when Windows has blocked `GenericServiceExtension.dll` or `ServiceExtension.dll`, typically because they came from an installation source that was downloaded or copied from another machine, so the .NET Framework refuses to load them from what it treats as an untrusted location. To resolve it:

    1. On the Quali Server, open PowerShell as an administrator and unblock the extension files. Adjust the path if CloudShell is installed elsewhere:

           Unblock-File "C:\Program Files (x86)\QualiSystems\CloudShell\Server\Extensions\GenericServiceExtension.dll"
           Unblock-File "C:\Program Files (x86)\QualiSystems\CloudShell\Server\Extensions\ServiceExtension.dll"

       Alternatively, open the file properties of each DLL, select 'Unblock' and click 'OK'.
    1. Restart the Quali Server service so that the extensions are loaded again.

    To avoid the problem on future installations, unblock the downloaded CloudShell installation archive before extracting it.

* `Unable to execute 'PowerOff' command. The resource <resource> is not excluded`

    The power command was run outside of a reservation while the resource was not yet excluded. The service excludes the resource and waits for the change to be reported, so this error indicates that the exclude did not take effect within the timeout. The timeout and polling interval are the `EXCLUDE_WAIT_TIMEOUT` and `EXCLUDE_POLL_INTERVAL` constants at the top of `src/driver.py`.

* A resource is skipped with "no 'PowerOn' connected command" or "no 'PowerOff' connected command"

    Make sure the resource has a power port that is connected to a PDU resource which provides the matching command.

## License
[Apache License 2.0](https://github.com/QualiSystemsLab/AdminService-AutoPower/blob/master/LICENSE)

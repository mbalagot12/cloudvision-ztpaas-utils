# CloudVision ZTPaaS Utils

![Bootstrap Script Linting Check Badge][BOOTSTRAP_LINTING_CHECK]
![Python Tests Badge][PYTHON_TESTS]

## Introduction

Arista’s Zero Touch Provisioning is used to configure a switch without user intervention. Built to leverage Arista’s Extensible Operating System (EOS), ZTP as-a-Service provides a flexible solution to onboard EOS devices into CloudVision as-a-Service.

CloudVision ZTPaaS Utils hosts different tools and scripts to support the Zero Touch Provisioning on CVaaS.

## Bootstrap Script with a Token

Bootstrap script with a token provides an alternative way of ZTP enrolling an Arista device against CVaaS. The cluster URL and the organisation wide enrollment token can be supplied by the script as opposed to the two being supplied using a USB drive. A DHCP server co-located with the Arista device can be configured to serve this bootstrap script using the bootfile-name option. This bootstrap script, then, takes over and perform all the steps necessary to initate ZTP against the correct CVaaS cluster and tenant.

- Log in to the CVaaS cluster and generate a token using the "Generate" option under "Devices/Onboard Devices" menu

- Download the custom bootstrap script and modify the "USER INPUT" section to specify the cluster URL and the enrollment token:

        ########### USER INPUT ############
        cvAddr = "www.arista.io"
        enrollmentToken = "eyJhbGciOiJSUzI1Nixxx..."
        # Enter currentTimeDate format hh:mm:ss mm/dd/yyy or hh:mm:ss yyyy-mm-dd or ntp or NTP. Enclosed in double quotes
        # If NTP clock synchronization is desired, the default ntp servers are time.google.com, pool.ntp.org and their associated globally known IP addresses.
        currentTimeDate = ""
        # timezone PST8PDT MST7MDT CST6CDT EST5EDT are valid US Timezones. Default PST8PDT
        # Rest of the world check switch CLI. Use the command config>clock timezone ?
        set_timezone = "PST8PDT"

- Host the script on a server locally, and modify the DHCP server to point to this script via option-67/bootfile-name option

- Boot up the EOS device into ZTP mode. It should download the script and enroll with the desired CVaaS cluster against the correct tenant.

- For the cluster URL (cvAddr), please use "www.arista.io". The URL "www.arista.io" can be used for all clusters and the script will redirect to the correct cluster URL. Otherwise if any issues occur, the specific regional URL where the CVaaS tenant is deployed can be used. The following are the cluster URLs used in production:

| Region | URL |
|--------|-----|
| United States 1a | `www.arista.io` |
| United States 1b | `www.cv-prod-us-central1-b.arista.io`|
| United States 1c | `www.cv-prod-us-central1-c.arista.io`|
| Canada | `www.cv-prod-na-northeast1-b.arista.io` |
| Europe West 2| `www.cv-prod-euwest-2.arista.io` |
| Japan| `www.cv-prod-apnortheast-1.arista.io` |
| Australia | `www.cv-prod-ausoutheast-1.arista.io` |
| United Kingdon | `www.cv-prod-uk-1.arista.io` |

!!! Warning

    URLs without `www` are not supported.

## Troubleshooting tips

### ZTP-4-EXEC_SCRIPT_SIGNALED: Config script exited with an uncaught signal. Signal code: 1

This usually indicates a problem executing the config script. In most cases this happens when the script is edited on a Microsoft Windows machine due
to which each line is ending in `Windows(CR LF)` instead of `Unix(LF)`. There are multiple ways to replace `CR LF` with `LF`, one way is to use Notepad++,
click on Edit - EOL Conversion and select `Unix(LF)` and save the file. This is also described in [A Practical Guide to Zero Touch Provisioning (ZTP) in CloudVision as a Service (CVaaS)](https://arista.my.site.com/AristaCommunity/s/article/A-Practical-Guide-to-Zero-Touch-Provisioning-ZTP-in-Cloud-Vision-as-a-Service-CVaaS) Community central article.

[BOOTSTRAP_LINTING_CHECK]: https://github.com/aristanetworks/cloudvision-ztpaas-utils/actions/workflows/bootstrap-linting.yaml/badge.svg
[PYTHON_TESTS]: https://github.com/aristanetworks/cloudvision-ztpaas-utils/actions/workflows/python-tests.yaml/badge.svg

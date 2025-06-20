#!/usr/bin/python
# Copyright (c) 2021 Arista Networks, Inc.  All rights reserved.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the COPYING file.

import base64
import datetime
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
from SysdbHelperUtils import SysdbPathHelper
import Cell
import urlparse


############## USER INPUT #############
cvAddr = ""

# enrollment token to be copied from CVaaS Device Registration page
enrollmentToken = ""
# currentTimeDate format hh:mm:ss mm/dd/yyy or hh:mm:ss yyyy-mm-dd or ntp or NTP. Enclosed in double quotes
currentTimeDate = ""
# timezone PST8PDT MST7MDT CST6CDT EST5EDT are valid US Timezones. Default PST8PDT
# Rest of the world check switch CLI. Config>clock timezone ?
set_timezone = "PST8PDT"

# Add proxy url if device is behind proxy server, leave it as an empty string otherwise
cvproxy = ""

# eosUrl is an optional parameter, which needs to be added if
# - The EOS version is <4.24.1F
#    - For versions <4.23.2F, SysDbHelperUtils is not present on the device
#    - For versions <4.24.1F, -enrollOnly flag is not present on the TA version
#      i.e. TA versions < 1.9.0
# - `cvproxy` parameter value is provided and TA version is <1.19
# This needs to be a http URL pointing to a SWI image on the local network.
eosUrl = ""

'''
Specify the address of the ntp server that the bootstrap script must configure on the device,
which is to sync the clock before it reaches out to CV.
For example:
ntpServer = "ntp1.aristanetworks.com"
'''
ntpServer = ""

##############  CONSTANTS  ##############
SECURE_HTTPS_PORT = "443"
SECURE_TOKEN = "token-secure"
INGEST_TOKEN = "token"
TOKEN_FILE_PATH = "/tmp/token.tok"
BOOT_SCRIPT_PATH = "/tmp/bootstrap-script"
REDIRECTOR_PATH = "api/v3/services/arista.redirector.v1.AssignmentService/GetOne"
VERSION = "2.0.1"

##############  HELPER FUNCTIONS  ##############
proxies = {"https": cvproxy, "http": cvproxy}

logger = None
def setupLogger():
   global logger
   logger = logging.getLogger("customBootstrap")
   logger.setLevel(logging.DEBUG)
   try:
      handler = logging.handlers.SysLogHandler(address="/dev/log")
      logger.addHandler(handler)
   except socket.error:
      print("Error setting up logger.")
      logger = None


def log(msg):
   """Print message to terminal and log if logging is up"""
   print(msg)
   if logger:
      logger.critical(msg)


def monitorNtpSync():
   timeInterval = 10
   expo = 2
   for i in range(5):
      log("Polling NTP status.")
      try:
         ntpStatInfo = subprocess.call(["ntpstat"])
      except Exception as e:
         raise Exception("ntpstat command failed, err: {err}. Aborting".format(err=e))
      log("NTP sync status - {ntpStatInfo}".format(ntpStatInfo=str(ntpStatInfo)))
      if ntpStatInfo == 0:
         log("NTP sync complete.")
         return
      time.sleep(timeInterval)
      timeInterval *= expo
   raise Exception("NTP sync failed. Timing out.")


def getExpiryFromToken(token):
   try:
      # jwt token has 3 parts (header, payload, sign) separated by a '.'
      # payload has 'exp' field which contains the token expiry time in epoch
      token_payload = token.split(".")[1]
      token_payload_decoded = str(base64.b64decode(token_payload + "==").decode("utf-8"))
      payload = json.loads(token_payload_decoded)
      return payload["exp"], True
   except Exception as e:
      log("Could not parse the enrollment token, err: {err}".format(err=e))
      log("Continuing with ZTP.")
      return -1, False


class CliManager(object):
   """
   Used to execute commands in EOS shell.
   """
   FAST_CLI_BINARY = "/usr/bin/FastCli"

   def __init__(self):
      self.fastCliBinary = CliManager.FAST_CLI_BINARY
      self.confidenceCheck()

   def confidenceCheck(self):
      assert os.path.isfile(self.fastCliBinary), "FastCli Binary Not Found"

   def runCommands(self, cmdList):
      cmdStr = ""
      cmdOutput = ""
      rc = 0
      err = ""
      # The delimiter `\n` is shown in console logging in its octal representation(#012).
      # It makes the log hard to read. The delimiter is updated to ` \\n `.
      delimiter = " \\n "
      try:
         cmds = "\n".join(cmdList)
         cmdStr = delimiter.join(cmdList)

         log("Executing the commands: [{cmdStr}]".format(cmdStr=cmdStr))
         cmdOutput = subprocess.check_output(
            "echo -e '" + cmds + "' | " + self.fastCliBinary, shell=True,
            stderr=subprocess.STDOUT, universal_newlines=True )
      except subprocess.CalledProcessError as e:
         rc = e.returncode
         err = e.output
         log("Error running commands: [{cmdStr}], err: {err}".format(cmdStr=cmdStr, err=err))
         return (rc, err)

      if cmdOutput:
         for line in cmdOutput.split("\n"):
            if line.startswith("%"):
               err = cmdOutput
               log("Error running commands: [{cmdStr}], err: {err}".format(
                  cmdStr=cmdStr, err=err))
               return(1, err)

      return (0, cmdOutput)


def configureAndRestartNTP(ntpServer):
   """
   Stops and restarts ntp with a specified ntp server.
   """
   cli = CliManager()

   # Command to stop the ntp process
   stopNtpCmds = ["en", "configure", "no ntp", "exit"]
   rc, cmdOut = cli.runCommands(stopNtpCmds)
   if rc:
      err = "NTP server could not be stopped, err: {cmdOut}. Aborting".format(cmdOut=cmdOut)
      log(err)
      raise Exception(err)

   # Command to configure and restart ntp process.
   # Note: iburst flag is added for faster synchronization
   configureNtpCmds = ["en", "configure", "ntp server {ntpServer} prefer iburst".format(
      ntpServer=ntpServer), "exit"]
   rc, cmdOut = cli.runCommands(configureNtpCmds)
   if rc:
      err = "Could not restart NTP server, err: {cmdOut}. Aborting".format(cmdOut=cmdOut)
      log(err)
      raise Exception(err)

   # Polls and monitors ntpstat command for synchronization status with intervals
   monitorNtpSync()


def getKeyValueFromFile(filename, key):
   """
   Given a filepath and a key, getKeyValueFromFile searches for key=VALUE in it
   and returns the found value without any whitespaces. In case no/empty key specified,
   gives the first string in the first line of the file.
   """
   if not key:
      with open(filename, "r") as f:
         return f.readline().split()[0]
   else:
      with open(filename, "r") as f:
         lines = f.readlines()
         for line in lines:
            if key in line :
               return line.split("=")[1].rstrip("\n")
   return None
#
# Set the current time and date from the user input fields
def setCurrentTimeDate(currentTimeDate, set_timezone):
   set_cli_privilege = EapiClient(disableAaa=True, privLevel=15)
   clock_cmds = ['configure', 'clock timezone {}'.format(set_timezone), 'exit', 'clock set {}'.format(currentTimeDate)]
   set_clock = set_cli_privilege.runCmds(1, clock_cmds)
   assert(set_clock['result'] !=0), sys.exit('Switch clock was not set. Exiting')

# Set NTP clock synchronization
def setNTPsync():
   ntps = ['time.google.com', 'pool.ntp.org', '45.15.168.198', '216.239.35.4']
   i=0
   set_cli_privilege = EapiClient(disableAaa=True, privLevel=15)
   for i in range(len(ntps)):
      ntp_cmds = ['configure', 'ntp server {}'.format(ntps[i]), 'exit']
      config_ntp_server = set_cli_privilege.runCmds(1, ntp_cmds)
      assert(config_ntp_server['result'] !=0), sys.exit('NTP server was not configured successfully. Exiting')



def tryImageUpgrade(e):
   """
   Try to perform an EOS image upgrade to the EOS image version specified in the `eosUrl`.
   Raises the received exception back if `eosUrl` is not specified
   """
   cli = CliManager()
   if eosUrl == "":
      # Raise the received exception if eosUrl is empty
      log("Specify 'eosUrl' for EOS version upgrade")
      raise e

   # Install new image
   cmdList = ["enable", "install source {eosUrl} destination flash:/EOS.swi".format(eosUrl=eosUrl)]
   rc, cmdOut = cli.runCommands(cmdList)
   if rc:
      err = "Failed to upgrade EOS from {eosUrl}, err: {err}. Aborting.".format(
         eosUrl=eosUrl, err=cmdOut)
      log(err)
      raise Exception(err)

   # Reboot device
   cmdList = ["enable", "reload all now"]
   rc, cmdOut = cli.runCommands(cmdList)
   if rc:
      err = "Failed to reboot for image upgrade, err: {err}. Aborting.".format(err=cmdOut)
      log(err)
      raise Exception(err)


###################  MAIN SCRIPT  ###################

##########  IMPORT HANDLING  ##########
# Some or more of these imports could fail when running this script
# in python2 environment starting EOS 4.30.1 If that is the case, we try to run the
# script with python3. In case we cannot recover, the script will require "eosUrl" to
# perform an upgrade before it can proceed.
try:
   import Cell
   import requests
   from SysdbHelperUtils import SysdbPathHelper
except ImportError as e:
   if sys.version_info < (3,) and os.path.exists("/usr/bin/python3"):
      os.execl("/usr/bin/python3", "python3", os.path.abspath(__file__ ))
   else:
      log("Python3 not found. Attempting EOS version upgrade")
      tryImageUpgrade(e)

try:
   # This import will fail for EOS < 4.30.1, where #!/usr/bin/python
   # will run this in a Python2 environment
   from urllib.parse import urlparse
except ImportError:
   from urlparse import urlparse


class BootstrapManager(object):
   """
   Bootstrap Manager class to perform enrollment to download and execute the
   bootstrap script.
   """

   def __init__(self):
      super(BootstrapManager, self).__init__()
      self.bootstrapURL = None
      self.redirectorURL = None
      self.tokenType = None
      self.enrollAddr = None
      self.certificate = ""
      self.key = ""

      # setting Sysdb access variables
      sysname = os.environ.get("SYSNAME", "ar")
      self.pathHelper = SysdbPathHelper(sysname)

      # sysdb paths accessed
      self.cellID = str(Cell.cellId())
      self.mibStatus = self.pathHelper.getEntity("hardware/entmib")

   def getBootstrapURL(self, addr):
      # urlparse in py3 parses correctly only if the url is properly introduced by //
      if not (addr.startswith("//") or addr.startswith("http://") or
               addr.startswith("https://")):
         addr = "//" + addr
      if isinstance(self, CloudBootstrapManager):
         addr = addr.replace("apiserver", "www")
      addrURL = urlparse( addr )
      if addrURL.netloc == "":
         addrURL = addrURL._replace(path="", netloc=addrURL.path)
      if addrURL.path == "":
         addrURL = addrURL._replace(path="/ztp/bootstrap")
      if addrURL.scheme == "":
         if isinstance(self, CloudBootstrapManager):
            addrURL = addrURL._replace(scheme="https")
         else:
            addrURL = addrURL._replace(scheme="http")
      return addrURL

   ##################################################################################
   # Step 0: Redirect to the correct cluster url
   ##################################################################################
   def checkWithRedirector(self):
      if not self.redirectorURL:
         return

      try:

         payload = '{{"key": {{"system_id": "{serialNum}"}}}}'.format(
            serialNum=self.mibStatus.root.serialNum)

         headers = {"redirector_token": enrollmentToken}
         response = requests.post(self.redirectorURL.geturl(), data=payload,
                                    headers=headers, proxies=proxies)
         response.raise_for_status()
      except Exception as e:
         err = "No assignment found. Error talking to redirector: {err}".format(err=e)
         log(err)
         raise Exception(err)

      clusters = response.json()[0]["value"]["clusters"]["values"]
      assignment = clusters[0]["hosts"]["values"][0]
      self.bootstrapURL = self.getBootstrapURL(assignment)
      self.enrollAddr = self.bootstrapURL.netloc
      if not self.enrollAddr.endswith(SECURE_HTTPS_PORT):
         self.enrollAddr += ":" + SECURE_HTTPS_PORT
      self.enrollAddr = self.enrollAddr.replace("www", "apiserver")

      log("Step 0 done, redirected to the correct cluster URL")
      log("enrollAddr - {enrollAddr}".format(enrollAddr=self.enrollAddr))

##################################################################################
# step 1: get client certificate using the enrollment token
##################################################################################
   def getClientCerficates( self ):
      with open( TOKEN_FILE_PATH, "w" ) as f:
         f.write( enrollmentToken )

      # A timeout of 60 seconds is used with TerminAttr commands since in most
      # versions of TerminAttr, the command execution does not finish if a wrong
      # flag is specified leading to the catch block being never executed
      cmd = "timeout 60s /usr/bin/TerminAttr"
      cmd += " -cvauth {tokenType},{tokenFilePath}".format(
         tokenType=self.tokenType, tokenFilePath=TOKEN_FILE_PATH)
      cmd += " -cvaddr {enrollAddr}".format(enrollAddr=self.enrollAddr)
      cmd += " -enrollonly"

      # Use cvproxy only when it is specified, this is to ensure that if we are on
      # older version of EOS that doesn't support cvproxy flag, the script won't fail
      if cvproxy != "":
         cmd += " -cvproxy={cvproxy}".format(cvproxy=cvproxy)

      try:
         subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
      except subprocess.CalledProcessError as e:
         # If the above subprocess call times out, it means that -cvproxy
         # flag is not present in the TerminAttr version running on that device
         # Hence we have to do an image upgrade in this case.
         if e.returncode == 124: # timeout
            log("TerminAttr enrollment timed out, err: {err}".format(err=e.output))
            log("Attempting EOS version upgrade")
            tryImageUpgrade(e)
         else:
            log("Failed to retrieve certs, err: {err}".format(err=e.output))
            raise e

      log("Step 1 done, exchanged enrollment token for client certificates")

   ##################################################################################
   # Step 2: Get the path of stored client certificate
   ##################################################################################
   def getCertificatePaths( self ):
      # Timeout added for TerminAttr
      cmd = "timeout 60s /usr/bin/TerminAttr"
      cmd += " -cvaddr {enrollAddr}".format(enrollAddr=self.enrollAddr)
      cmd += " -certsconfig"

      try:
         response = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT,
                                             universal_newlines=True)
         json_response = json.loads(response)
         self.certificate = str(json_response[self.enrollAddr]["certFile"])
         self.key = str( json_response[self.enrollAddr]["keyFile"])
      except subprocess.CalledProcessError as e:
         log("Failed to get the path of the stored client certs, err: {err}".format(
            err=e.output))
         log("Using fallback paths for client certs")
         basePath = "/persist/secure/ssl/terminattr/primary"
         self.certificate = "{basePath}/certs/client.crt".format(basePath=basePath)
         self.key = "{basePath}/keys/client.key".format(basePath=basePath)

      print( "step 2 done, obtained client certificates location from TA" )
      print( "ceriticate location: " + self.certificate )
      print( "key location: " + self.key )

   ##################################################################################
   # Step 3.1: Get bootstrap script using the certificates
   ##################################################################################
   def getBootstrapScript( self ):
      # setting Sysdb access variables
      sysname = os.environ.get( "SYSNAME", "ar" )
      pathHelper = SysdbPathHelper( sysname )

      # sysdb paths accessed
      cellID = str( Cell.cellId() )
      mibStatus = pathHelper.getEntity( "hardware/entmib" )
      tpmStatus = pathHelper.getEntity( "cell/" + cellID + "/hardware/tpm/status" )
      tpmConfig = pathHelper.getEntity( "cell/" + cellID + "/hardware/tpm/config" )

      # setting header information
      headers = {}
      headers["X-Arista-SystemMAC"] = self.mibStatus.systemMacAddr
      headers["X-Arista-ModelName"] = self.mibStatus.root.modelName
      headers["X-Arista-HardwareVersion"] = self.mibStatus.root.hardwareRev
      headers["X-Arista-Serial"] = self.mibStatus.root.serialNum

      try:
         tpmStatus = self.pathHelper.getEntity("cell/{cellID}/hardware/tpm/status".format(
            cellID=self.cellID))
         headers["X-Arista-TpmApi"] = tpmStatus.tpmVersion
         headers["X-Arista-TpmFwVersion"] = tpmStatus.firmwareVersion
         headers["X-Arista-SecureZtp"] = str(tpmStatus.boardValidated)
      except Exception as e:
         log("Exception while getting device tpmStatus: {err}".format(err=e))

      headers["X-Arista-SoftwareVersion"] = getKeyValueFromFile("/etc/swi-version",
                                                                  "SWI_VERSION")
      headers["X-Arista-Architecture"] = getKeyValueFromFile("/etc/arch", "")
      headers["X-Arista-CustomBootScriptVersion"] = VERSION

      # Making the request and writing to file
      response = requests.get(self.bootstrapURL.geturl(), headers=headers,
                              cert=(self.certificate, self.key), proxies=proxies)
      response.raise_for_status()
      with open(BOOT_SCRIPT_PATH, "w") as f:
         f.write(response.text)

      log("Step 3.1 done, bootstrap script fetched and stored at {bootScriptPath}".format(
         bootScriptPath=BOOT_SCRIPT_PATH))

   ##################################################################################
   # Step 3.2: Execute the downloaded bootstrap script
   ##################################################################################
   def executeBootstrap( self ):
      proc = None
      def handleSigterm(signum, frame):
         if proc is not None:
            proc.terminate()
         sys.exit(127 + signal.SIGTERM)

      # The bootstrap script and challenge script anyway contain the required shebang for a
      # particular EOS version, hence instead of re-evaluating here, we can easily just execute
      # it from that shebang itself.
      cmd = ["chmod +x {bootScriptPath}".format(bootScriptPath=BOOT_SCRIPT_PATH)]
      try:
         subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
      except subprocess.CalledProcessError as e:
         log(e.output)
         raise e
      log("Step 3.2.1 done, execution permissions for bootstrap script setup")

      cmd = BOOT_SCRIPT_PATH
      os.environ["CVPROXY"] = cvproxy
      try:
         signal.signal(signal.SIGTERM, handleSigterm)
         proc = subprocess.Popen([cmd], shell=True, stderr=subprocess.STDOUT, env=os.environ)
         proc.communicate()
         if proc.returncode:
            log("Bootstrap script failed with return code {rc}".format(rc=proc.returncode))
            sys.exit(proc.returncode)
      except subprocess.CalledProcessError as e:
         log(e.output)
         raise e
      log("Step 3.2.2 done, executed the fetched bootstrap script")

   def run( self ):
      self.getClientCerficates()
      self.getCertificatePaths()
      self.getBootstrapScript()
      self.executeBootstrap()


class CloudBootstrapManager(BootstrapManager):
   """
   Bootstrap Manager class for cloud deployments.
   """

   def __init__(self):
      super(CloudBootstrapManager, self).__init__()
      self.bootstrapURL = self.getBootstrapURL(cvAddr)
      self.redirectorURL = self.bootstrapURL._replace(path=REDIRECTOR_PATH)
      self.tokenType = SECURE_TOKEN
      self.enrollAddr = None


class OnPremBootstrapManager(BootstrapManager):
   """
   Bootstrap Manager class for on-prem deployments.
   """

   def __init__(self):
      super(OnPremBootstrapManager, self).__init__()
      self.bootstrapURL = self.getBootstrapURL(cvAddr)
      self.redirectorURL = None
      self.tokenType = INGEST_TOKEN
      self.enrollAddr = self.bootstrapURL.netloc


if __name__ == "__main__":
   setupLogger()

   # Logging the current version of the custom bootstrap script
   log("Current Custom Bootstrap Script Version: {version}".format(version=VERSION))

   if cvAddr == "":
      err = "Error: address to CVP missing"
      log(err)
      sys.exit(err)
   if enrollmentToken == "":
      sys.exit( "error: enrollment token missing" )

   # Check whether it is cloud or on prem
   if cvAddr.find("arista.io") != -1:
      bm = CloudBootstrapManager()
   else:
      bm = OnPremBootstrapManager()

   # Run the script
   bm.run()

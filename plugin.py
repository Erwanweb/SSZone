"""
CASA-IA Security system zone python plugin for Domoticz
Author: Erwanweb,
Version:    0.0.1: alpha
            0.0.2: beta
"""
"""
<plugin key="SSZone" name="AC Security zone" author="Erwanweb" version="0.0.2" externallink="https://github.com/Erwanweb/BoilerCLite.git">
    <description>
        <h2>Security zone for CASA-IA Security system</h2><br/>
        Easily control security zone<br/>
        <h3>Set-up and Configuration</h3>
    </description>
    <params>
        <param field="Address" label="Domoticz IP Address" width="200px" required="true" default="127.0.0.1"/>
        <param field="Port" label="Port" width="40px" required="true" default="8080"/>
        <param field="Username" label="Username" width="200px" required="false" default=""/>
        <param field="Password" label="Password" width="200px" required="false" default=""/>
        <param field="Mode1" label="MS or door/window Sensors (csv list of idx)" width="100px" required="true" default=""/>
        <param field="Mode5" label="Detection delay, Alarm On delay, Alarm Off delay (all in seconds)" width="100px" required="true" default="10,30,0"/>
        <param field="Mode6" label="Logging Level" width="200px">
            <options>
                <option label="Normal" value="Normal"  default="true"/>
                <option label="Verbose" value="Verbose"/>
                <option label="Debug - Python Only" value="2"/>
                <option label="Debug - Basic" value="62"/>
                <option label="Debug - Basic+Messages" value="126"/>
                <option label="Debug - Connections Only" value="16"/>
                <option label="Debug - Connections+Queue" value="144"/>
                <option label="Debug - All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz
import json
import urllib.parse as parse
import urllib.request as request
from datetime import datetime, timedelta
import time
import base64
import itertools

class deviceparam:

    def __init__(self, unit, nvalue, svalue):
        self.unit = unit
        self.nvalue = nvalue
        self.svalue = svalue


class BasePlugin:

    def __init__(self):

        self.debug = False
        self.Detectiondelay = 10
        self.alarmondelay = 30
        self.alarmoffdelay = 0
        self.DTAlarm = []
        self.NewDetection = False
        self.Detection = False
        self.Alarm = False
        self.Alarmtempo = datetime.now()
        self.detectionchangedtime = datetime.now()
        self.loglevel = None
        self.statussupported = True
        return


    def onStart(self):

        # setup the appropriate logging level
        try:
            debuglevel = int(Parameters["Mode6"])
        except ValueError:
            debuglevel = 0
            self.loglevel = Parameters["Mode6"]
        if debuglevel != 0:
            self.debug = True
            Domoticz.Debugging(debuglevel)
            DumpConfigToLog()
            self.loglevel = "Verbose"
        else:
            self.debug = False
            Domoticz.Debugging(0)

        # create the child devices if these do not exist yet
        devicecreated = []
        if 1 not in Devices:
            Domoticz.Device(Name="Surveillance", Unit=1, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(1, 0, ""))  # default is Off
        if 2 not in Devices:
            Domoticz.Device(Name="Detection", Unit=2, TypeName="Switch", Image=13, Used=1).Create()
            devicecreated.append(deviceparam(2, 0, ""))  # default is Off
        if 3 not in Devices:
            Domoticz.Device(Name="Zone Alarm", Unit=3, TypeName="Switch", Image=13, Used=1).Create()
            devicecreated.append(deviceparam(3, 0, ""))  # default is Off

        # if any device has been created in onStart(), now is time to update its defaults
        for device in devicecreated:
            Devices[device.unit].Update(nValue=device.nvalue, sValue=device.svalue)

        # build lists of alarm sensors
        self.DTAlarm = parseCSV(Parameters["Mode1"])
        Domoticz.Debug("Alarm Sensors for this Zone = {}".format(self.DTAlarm))

        # splits additional parameters
        params = parseCSV(Parameters["Mode5"])
        if len(params) == 3:
            self.Detectiondelay = CheckParam("delay before detection validation)",params[0],5)
            self.Alarmondelay = CheckParam("Alarm On Delay",params[1],30)
            self.Alarmoffdelay = CheckParam("Alarm Off Delay",params[1],0)

        else:
            Domoticz.Error("Error reading Mode5 parameters")

        # if mode = off then make sure actual Alarm is off just in case if was manually set to on
        if Devices[1].sValue == "0":
            self.Alarm(False)


    def onStop(self):

        Domoticz.Debugging(0)


    def onCommand(self, Unit, Command, Level, Color):

        Domoticz.Debug("onCommand called for Unit {}: Command '{}', Level: {}".format(Unit, Command, Level))

        if (Unit == 1):
            if (Command == "On"):
                Devices[1].Update(nValue = 1,sValue= Devices[1].sValue)
            else:
                Devices[1].Update(nValue = 0,sValue = Devices[1].sValue)
                self.NewDetection = False
                self.Detection = False
                self.Alarm = False
                Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)


    def onHeartbeat(self):

        # fool proof checking....
        if not all(device in Devices for device in (1,2,3)):
            Domoticz.Error("one or more devices required by the plugin is/are missing, please check domoticz device creation settings and restart !")
            return

        if Devices[1].sValue == "0":  # Surveillance is off
            if self.Alarm:  # Surveillance setting was just changed so we kill the surveillance of the zone and stop the eventual alarm
                self.NewDetection = False
                self.Detection = False
                self.Alarm = False
                Devices[2].Update(nValue = 0,sValue = Devices[2].sValue)
                Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)
                Domoticz.Debug("Switching Surveillance Off and so alarm Off !")

        else:
            self.AlarmDetection()


    def AlarmDetection(self):

        Domoticz.Log("Alarm Detection called")

        now = datetime.now()

        if Devices[1].sValue == "0":  # Surveillance is off
            Domoticz.Log("Zone Surveillance desactived...")
            self.NewDetection = False
            self.Detection = False
            self.Alarm = False
            Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)

        else:
            Domoticz.Log("Zone surveillance Actived...")

            # Build list of Alarm sensor (switches), with their current status
            AlarmDT = {}
            devicesAPI = DomoticzAPI("type=devices&filter=light&used=true&order=Name")
            if devicesAPI:
                for device in devicesAPI["result"]:  # parse the presence/motion sensors (switch) device
                    idx = int(device["idx"])
                    if idx in self.DTAlarm:  # this is one of our presence/motion sensors
                        if "Status" in device:
                            AlarmDT[idx] = True if device["Status"] == "On" else False
                            Domoticz.Debug("DT switch {} currently is '{}'".format(idx,device["Status"]))
                            if device["Status"] == "On":
                                self.Alarmtempo = datetime.now()

                        else:
                            Domoticz.Error("Device with idx={} does not seem to be a DT !".format(idx))

            # fool proof checking....
            if len(AlarmDT) == 0:
                Domoticz.Error("none of the devices in the 'MS or door/window sensor' parameter is a switch... no action !")
                self.NewDetection = False
                self.Detection = False
                self.Alarm = False
                Devices[1].Update(nValue = 0,sValue = Devices[1].sValue)
                Devices[2].Update(nValue = 0,sValue = Devices[2].sValue)
                Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)
                return

            if self.Alarmtempo + timedelta(seconds = self.Detectiondelay) >= now:
                self.NewDetection = True
                Domoticz.Log("At mini 1 Alarm sensor is ON or was ON in detection delay...")
            else:
                self.NewDetection = False

            if self.NewDetection:
                if Devices[2].nValue == 1:
                    Domoticz.Log("There is detection but already registred...")
                else:
                    Domoticz.Log("New Detection...")
                    Devices[2].Update(nValue = 1,sValue = Devices[2].sValue)
                    self.Detection = True
                    self.detectionchangedtime = datetime.now()

            else:
                if Devices[2].nValue == 0:
                    Domoticz.Log("No Detection...")
                else:
                    Domoticz.Log("No Detection registred in detection delay...")
                    Devices[2].Update(nValue = 0,sValue = Devices[2].sValue)
                    self.Detection = False
                    self.detectionchangedtime = datetime.now()

            if self.Detection:
                if not self.Alarm:
                    if self.detectionchangedtime + timedelta(seconds = self.Alarmondelay) <= now:
                        Domoticz.Log("New detection : Alarm !")
                        self.Alarm = True
                        Devices[3].Update(nValue = 1,sValue = Devices[3].sValue)

                    else:
                        Domoticz.Log("New detection : Alarm is INACTIVE but in timer ON period !")
                elif self.Alarm:
                    Domoticz.Log("Alarm is already ACTIVE !")
            else:
                if self.Alarm:
                    if self.detectionchangedtime + timedelta(seconds = self.alarmoffdelay) <= now:
                        Domoticz.Log("Alarm is now INACTIVE because no Detection in timer OFF period !")
                        self.Alarm = False
                        Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)

                    else:
                        Domoticz.Log("Alarm is ACTIVE but in timer OFF period !")
                else:
                    Domoticz.Log("No Detection, All OK, No Alarm !")

    def WriteLog(self, message, level="Normal"):

        if self.loglevel == "Verbose" and level == "Verbose":
            Domoticz.Log(message)
        elif level == "Normal":
            Domoticz.Log(message)



global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onCommand(Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Color)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()


# Plugin utility functions ---------------------------------------------------

def parseCSV(strCSV):

    listvals = []
    for value in strCSV.split(","):
        try:
            val = int(value)
        except:
            pass
        else:
            listvals.append(val)
    return listvals


def DomoticzAPI(APICall):

    resultJson = None
    url = "http://{}:{}/json.htm?{}".format(Parameters["Address"], Parameters["Port"], parse.quote(APICall, safe="&="))
    Domoticz.Debug("Calling domoticz API: {}".format(url))
    try:
        req = request.Request(url)
        if Parameters["Username"] != "":
            Domoticz.Debug("Add authentification for user {}".format(Parameters["Username"]))
            credentials = ('%s:%s' % (Parameters["Username"], Parameters["Password"]))
            encoded_credentials = base64.b64encode(credentials.encode('ascii'))
            req.add_header('Authorization', 'Basic %s' % encoded_credentials.decode("ascii"))

        response = request.urlopen(req)
        if response.status == 200:
            resultJson = json.loads(response.read().decode('utf-8'))
            if resultJson["status"] != "OK":
                Domoticz.Error("Domoticz API returned an error: status = {}".format(resultJson["status"]))
                resultJson = None
        else:
            Domoticz.Error("Domoticz API: http error = {}".format(response.status))
    except:
        Domoticz.Error("Error calling '{}'".format(url))
    return resultJson


def CheckParam(name, value, default):

    try:
        param = int(value)
    except ValueError:
        param = default
        Domoticz.Error("Parameter '{}' has an invalid value of '{}' ! defaut of '{}' is instead used.".format(name, value, default))
    return param


# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug("'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return
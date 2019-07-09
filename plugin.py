"""
Smart Virtual Thermostat for ON/OFF Heaters python plugin for Domoticz
Author: Erwanweb,
        adapted from the SVT By Logread V0.4.4 and Anthor, see:
            https://github.com/999LV/SmartVirtualThermostat
            http://www.antor.fr/apps/smart-virtual-thermostat-eng-2/?lang=en
            https://github.com/AntorFr/SmartVT
Version:    0.0.1: alpha
            0.0.2: beta
"""
"""
<plugin key="AC Security" name="AC Security system" author="Erwanweb" version="0.0.2" externallink="https://github.com/Erwanweb/ACSS.git">
    <description>
        <h2>Smart Virtual Thermostat for ON/OFF heater</h2><br/>
        Easily implement in Domoticz an advanced virtual thermostat based on time modulation<br/>
        and self learning of relevant room thermal characteristics (including insulation level)<br/>
        rather then more conventional hysteresis methods, so as to achieve a greater comfort.<br/>
        <h3>Set-up and Configuration</h3>
    </description>
    <params>
        <param field="Address" label="Domoticz IP Address" width="200px" required="true" default="127.0.0.1"/>
        <param field="Port" label="Port" width="40px" required="true" default="8080"/>
        <param field="Username" label="Username" width="200px" required="false" default=""/>
        <param field="Password" label="Password" width="200px" required="false" default=""/>
        <param field="Mode1" label="Perimeter Sensors (csv list of idx)" width="100px" required="false" default="0"/>
        <param field="Mode2" label="Outside Presence Sensors (csv list of idx)" width="100px" required="false" default=""/>
        <param field="Mode3" label="Inside Presence Sensors (csv list of idx)" width="100px" required="true" default=""/>
        <param field="Mode4" label="Panic buton (csv list of idx)" width="100px" required="false" default=""/>
        <param field="Mode5" label="Arming On delay, Access delay, Siren cycle time (all in secondes)" width="200px" required="true" default="3,1,3"/>
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
        self.calculate_period = 30  # Time in minutes between two calculations (cycle)
        self.minheatpower = 0  # if heating is needed, minimum heat power (in % of calculation period)
        self.deltamax = 0.2  # allowed temp excess over setpoint temperature
        self.pauseondelay = 2  # time between pause sensor actuation and actual pause
        self.pauseoffdelay = 1  # time between end of pause sensor actuation and end of actual pause
        self.forcedduration = 60  # time in minutes for the forced mode
        self.ActiveSensors = {}
        self.InTempSensors = []
        self.OutTempSensors = []
        self.Heaters = []
        self.InternalsDefaults = {
            'ConstC': 60,  # inside heating coeff, depends on room size & power of your heater (60 by default)
            'ConstT': 1,  # external heating coeff,depends on the insulation relative to the outside (1 by default)
            'nbCC': 0,  # number of learnings for ConstC
            'nbCT': 0,  # number of learnings for ConstT
            'LastPwr': 0,  # % power from last calculation
            'LastInT': 0,  # inside temperature at last calculation
            'LastOutT': 0,  # outside temprature at last calculation
            'LastSetPoint': 20,  # setpoint at time of last calculation
            'ALStatus': 0}  # AutoLearning status (0 = uninitialized, 1 = initialized, 2 = disabled)
        self.Internals = self.InternalsDefaults.copy()
        self.heat = False
        self.pause = False
        self.pauserequested = False
        self.pauserequestchangedtime = datetime.now()
        self.forced = False
        self.intemp = 20.0
        self.outtemp = 20.0
        self.setpoint = 20.0
        self.endheat = datetime.now()
        self.nextcalc = self.endheat
        self.lastcalc = self.endheat
        self.nextupdate = self.endheat
        self.nexttemps = self.endheat
        self.DTpresence = []
        self.Presencemode = False
        self.Presence = False
        self.PresenceTH = False
        self.presencechangedtime = datetime.now()
        self.PresenceDetected = False
        self.DTtempo = datetime.now()
        self.presenceondelay = 2  # time between first detection and last detection before turning presence ON
        self.presenceoffdelay = 3  # time between last detection before turning presence OFF
        self.learn = True
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
            Options = {"LevelActions": "||",
                       "LevelNames": "Off|Disarmed|Perimeter|Total",
                       "LevelOffHidden": "true",
                       "SelectorStyle": "0"}
            Domoticz.Device(Name="Security mode", Unit=1, TypeName="Selector Switch", Switchtype=18, Image=15,
                            Options=Options, Used=1).Create()
            devicecreated.append(deviceparam(1, 0, "0"))  # default is Off state
        if 2 not in Devices:
            Domoticz.Device(Name="Perimeter Protection", Unit=2, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(2, 0, ""))  # default is Off
        if 3 not in Devices:
            Domoticz.Device(Name="Total Protection", Unit=3, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(3, 0, ""))  # default is Off
        if 4 not in Devices:
            Domoticz.Device(Name="Alarm", Unit=4, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(4, 0, ""))  # default is Off
        if 5 not in Devices:
            Domoticz.Device(Name="Perimeter sensors", Unit=5, TypeName="Switch", Image=9).Create()
            devicecreated.append(deviceparam(5, 0, ""))  # default is Off
        if 6 not in Devices:
            Domoticz.Device(Name="Outdoor Presence sensors", Unit=6, TypeName="Switch", Image=9).Create()
            devicecreated.append(deviceparam(6, 0, ""))  # default is Off
        if 7 not in Devices:
            Domoticz.Device(Name="Indoor Presence sensors", Unit=7, TypeName="Switch", Image=9).Create()
            devicecreated.append(deviceparam(7, 0, ""))  # default is Off

        # if any device has been created in onStart(), now is time to update its defaults
        for device in devicecreated:
            Devices[device.unit].Update(nValue=device.nvalue, sValue=device.svalue)

        # build lists of sensors and switches
        self.InTempSensors = parseCSV(Parameters["Mode1"])
        Domoticz.Debug("Inside Temperature sensors = {}".format(self.InTempSensors))
        self.OutTempSensors = parseCSV(Parameters["Mode2"])
        Domoticz.Debug("Outside Temperature sensors = {}".format(self.OutTempSensors))
        self.Heaters = parseCSV(Parameters["Mode3"])
        Domoticz.Debug("Heaters = {}".format(self.Heaters))
        self.DTpresence = parseCSV(Parameters["Mode4"])
        Domoticz.Debug("DTpresence = {}".format(self.DTpresence))
        
        # build dict of status of all temp sensors to be used when handling timeouts
        for sensor in itertools.chain(self.InTempSensors, self.OutTempSensors):
            self.ActiveSensors[sensor] = True

        # splits additional parameters
        params = parseCSV(Parameters["Mode5"])
        if len(params) == 7:
            self.calculate_period = CheckParam("Calculation Period", params[0], 30)
            if self.calculate_period < 5:
                Domoticz.Error("Invalid calculation period parameter. Using minimum of 5 minutes !")
                self.calculate_period = 5
            self.minheatpower = CheckParam("Minimum Heating (%)", params[1], 0)
            if self.minheatpower > 100:
                Domoticz.Error("Invalid minimum heating parameter. Using maximum of 100% !")
                self.minheatpower = 100
            self.pauseondelay = CheckParam("Pause On Delay", params[2], 2)
            self.pauseoffdelay = CheckParam("Pause Off Delay", params[3], 0)
            self.forcedduration = CheckParam("Forced Mode Duration", params[4], 60)
            if self.forcedduration < 30:
                Domoticz.Error("Invalid forced mode duration parameter. Using minimum of 30 minutes !")
                self.calculate_period = 30
            self.presenceondelay = CheckParam("Presence On Delay", params[5], 2)
            self.presenceoffdelay = CheckParam("Presence Off Delay",params[6],3)
        else:
            Domoticz.Error("Error reading Mode5 parameters")

        # loads persistent variables from dedicated user variable
        # note: to reset the thermostat to default values (i.e. ignore all past learning),
        # just delete the relevant "<plugin name>-InternalVariables" user variable Domoticz GUI and restart plugin
        self.getUserVar()

        # if mode = off then make sure actual heating is off just in case if was manually set to on
        if Devices[1].sValue == "0":
            self.switchHeat(False)


    def onStop(self):

        Domoticz.Debugging(0)


    def onCommand(self, Unit, Command, Level, Color):

        Domoticz.Debug("onCommand called for Unit {}: Command '{}', Level: {}".format(Unit, Command, Level))

        if Unit == 3:  # pause switch
            self.pauserequestchangedtime = datetime.now()
            svalue = ""
            if str(Command) == "On":
                nvalue = 1
                self.pauserequested = True
            else:
                nvalue = 0
                self.pauserequested = False

        else:
            nvalue = 1 if Level > 0 else 0
            svalue = str(Level)

        Devices[Unit].Update(nValue=nvalue, sValue=svalue)

        if Unit in (1, 2, 4, 5): # force recalculation if control or mode or a setpoint changed
            self.nextcalc = datetime.now()
            self.learn = False
            self.onHeartbeat()


    def onHeartbeat(self):

        self.PresenceDetection()

        # fool proof checking.... based on users feedback
        if not all(device in Devices for device in (1,2,3,4,5,6,7,8)):
            Domoticz.Error("one or more devices required by the plugin is/are missing, please check domoticz device creation settings and restart !")
            return


    def Heatingrequest(self):

        if Parameters["Mode4"] == "":
            Domoticz.Debug("perimeter protection mode = NO...")
            self.PerimeterALARM = False
            Devices[5].Update(nValue = 0,sValue = Devices[5].sValue)

        else:
            Domoticz.Debug("perimeter protection mode = YES...")


             # Build list of Perimeter sensors, with their current status
             PerimeterDT = {}
             devicesAPI = DomoticzAPI("type=devices&filter=light&used=true&order=Name")
             if devicesAPI:
                for device in devicesAPI["result"]:  # parse the presence/motion sensors (DT) device
                    idx = int(device["idx"])
                    if idx in self.DTpresence:  # this is one of our DT
                        if "Status" in device:
                            PerimeterDT[idx] = True if device["Status"] == "On" else False
                            Domoticz.Debug("DT switch {} currently is '{}'".format(idx,device["Status"]))
                            if device["Status"] == "On":
                                self.PerimeterALARM = True

                        else:
                            Domoticz.Error("Device with idx={} does not seem to be a DT !".format(idx))


             # fool proof checking....
             if len(PerimeterDT) == 0:
                Domoticz.Error("none of the devices in the 'dt' parameter is a dt... no action !")
                self.PerimeterALARM = False
                Devices[5].Update(nValue = 0,sValue = Devices[5].sValue)
                return

             if Devices[2].nValue == 0:
                Domoticz.Debug("perimeter protection not armed...")
                self.PerimeterALARM = False

             else:
                Domoticz.Debug("perimeter protection armed !")

                  if self.PerimeterALARM:
                      if Devices[5].nValue == 1:
                        Domoticz.Debug("Perimeter intrusion detected but already registred...")
                      else:
                        Domoticz.Debug("Perimeter intrusion detected !!!!!")
                        Devices[5].Update(nValue = 1,sValue = Devices[5].sValue)
                  else:
                       Domoticz.Debug("Perimeter protection is OK !")
                       Devices[5].Update(nValue = 0,sValue = Devices[5].sValue)




    def switchHeat(self, switch):

        # Build list of heater switches, with their current status,
        # to be used to check if any of the heaters is already in desired state
        switches = {}
        devicesAPI = DomoticzAPI("type=devices&filter=light&used=true&order=Name")
        if devicesAPI:
            for device in devicesAPI["result"]:  # parse the switch device
                idx = int(device["idx"])
                if idx in self.Heaters:  # this switch is one of our heaters
                    if "Status" in device:
                        switches[idx] = True if device["Status"] == "On" else False
                        Domoticz.Debug("Heater switch {} currently is '{}'".format(idx, device["Status"]))
                    else:
                        Domoticz.Error("Device with idx={} does not seem to be a switch !".format(idx))

        # fool proof checking.... based on users feedback
        if len(switches) == 0:
            Domoticz.Error("none of the devices in the 'heaters' parameter is a switch... no action !")
            return

        # flip on / off as needed
        self.heat = switch
        command = "On" if switch else "Off"
        Domoticz.Debug("Heating '{}'".format(command))
        for idx in self.Heaters:
            if switches[idx] != switch:  # check if action needed
                DomoticzAPI("type=command&param=switchlight&idx={}&switchcmd={}".format(idx, command))
        if switch:
            Domoticz.Debug("Heating requested at Boiler")




    def InsidePresenceDetection(self):

        if Devices[3].nValue == 0:
            Domoticz.Debug("Inside DT protection not armed...")
            self.PerimeterALARM = False

        else:
            Domoticz.Debug("Inside DT protection armed !")

                # Build list of Perimeter sensors, with their current status
                PerimeterDT = {}
                devicesAPI = DomoticzAPI("type=devices&filter=light&used=true&order=Name")
                if devicesAPI:
                    for device in devicesAPI["result"]:  # parse the presence/motion sensors (DT) device
                        idx = int(device["idx"])
                        if idx in self.DTpresence:  # this is one of our DT
                            if "Status" in device:
                                PerimeterDT[idx] = True if device["Status"] == "On" else False
                                Domoticz.Debug("DT switch {} currently is '{}'".format(idx,device["Status"]))
                                if device["Status"] == "On":
                                    self.PerimeterALARM = True

                            else:
                                Domoticz.Error("Device with idx={} does not seem to be a DT !".format(idx))


                # fool proof checking....
                if len(PerimeterDT) == 0:
                   Domoticz.Error("none of the devices in the 'dt' parameter is a dt... no action !")
                   self.PerimeterALARM = False
                   Devices[5].Update(nValue = 0,sValue = Devices[5].sValue)
                   return

                if self.PerimeterALARM:
                    if Devices[5].nValue == 1:
                        Domoticz.Debug("Perimeter intrusion detected but already registred...")
                    else:
                        Domoticz.Debug("Perimeter intrusion detected !!!!!")
                        Devices[5].Update(nValue = 1,sValue = Devices[5].sValue)
                else:
                    Domoticz.Debug("Perimeter protection is OK !")
                    Devices[5].Update(nValue = 0,sValue = Devices[5].sValue)


    def WriteLog(self, message, level="Normal"):

        if self.loglevel == "Verbose" and level == "Verbose":
            Domoticz.Log(message)
        elif level == "Normal":
            Domoticz.Log(message)

    def SensorTimedOut(self, idx, name, datestring):

        def LastUpdate(datestring):
            dateformat = "%Y-%m-%d %H:%M:%S"
            # the below try/except is meant to address an intermittent python bug in some embedded systems
            try:
                result = datetime.strptime(datestring, dateformat)
            except TypeError:
                result = datetime(*(time.strptime(datestring, dateformat)[0:6]))
            return result

        timedout = LastUpdate(datestring) + timedelta(minutes=int(Settings["SensorTimeout"])) < datetime.now()

        # handle logging of time outs... only log when status changes (less clutter in logs)
        if timedout:
            if self.ActiveSensors[idx]:
                Domoticz.Error("skipping timed out temperature sensor '{}'".format(name))
                self.ActiveSensors[idx] = False
        else:
            if not self.ActiveSensors[idx]:
                Domoticz.Status("previously timed out temperature sensor '{}' is back online".format(name))
                self.ActiveSensors[idx] = True

        return timedout


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
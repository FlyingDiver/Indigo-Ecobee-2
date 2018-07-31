#! /usr/bin/env python
# -*- coding: utf-8 -*-

import pyecobee
import sys
import json
import time
import indigo
from ecobee_devices import *
import temperature_scale
import logging
from indigo_logging_handler import IndigoLoggingHandler

logging.getLogger("requests").setLevel(logging.WARNING)

DEBUG=False
ACCESS_TOKEN_PLUGIN_PREF='accessToken'
AUTHORIZATION_CODE_PLUGIN_PREF='authorizationCode'
REFRESH_TOKEN_PLUGIN_PREF='refreshToken'
TEMPERATURE_SCALE_PLUGIN_PREF='temperatureScale'

API_KEY = "qyy0od74EpMz2P8X1fmAfyoxKod4t1Fo"

TEMP_FORMATTERS = {
    'F': temperature_scale.Fahrenheit(),
    'C': temperature_scale.Celsius(),
    'K': temperature_scale.Kelvin(),
    'R': temperature_scale.Rankine()
}

#   Plugin-enforced minimum and maximum setpoint ranges per temperature scale
ALLOWED_RANGE = {
    'F': (40,95),
    'C': (6,35),
    'K': (277,308),
    'R': (500,555)
}

REFRESH_INTERVAL = 45.0 * 60.0

# constrain a value to a range
def clamp(n, minn, maxn): return min(max(n, minn), maxn)

class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        self.debug = DEBUG

        self.active_remote_sensors = []
        self.active_thermostats = []
        self.active_smart_thermostats = []

        logHandler = IndigoLoggingHandler(self)

        pyecobeeLogger = logging.getLogger('pyecobee')
        pyecobeeLogger.addHandler(logHandler)
        self.log = logging.getLogger('indigo.ecobee.plugin')
        self.log.addHandler(logHandler)

        try:
            self.logLevel = int(self.pluginPrefs[u"logLevel"])
        except:
            self.logLevel = logging.INFO
        pyecobeeLogger.setLevel(self.logLevel)
        self.log.setLevel(self.logLevel)
        self.log.debug(u"logLevel = " + str(self.logLevel))

        if TEMPERATURE_SCALE_PLUGIN_PREF in pluginPrefs:
            self._setTemperatureScale(pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF][0])
        else:
            self._setTemperatureScale('F')

        tmpconfig = {'API_KEY': API_KEY}
        if ACCESS_TOKEN_PLUGIN_PREF in pluginPrefs:
            tmpconfig['ACCESS_TOKEN'] = pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF]
        if AUTHORIZATION_CODE_PLUGIN_PREF in pluginPrefs:
            tmpconfig['AUTHORIZATION_CODE'] = pluginPrefs[AUTHORIZATION_CODE_PLUGIN_PREF]
        if REFRESH_TOKEN_PLUGIN_PREF in pluginPrefs:
            tmpconfig['REFRESH_TOKEN'] = pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF]
        self.log.debug(u"constructed pyecobee config: %s" % json.dumps(tmpconfig))
        
        # Create an ecobee object with the config dictionary
        self.ecobee = pyecobee.Ecobee(config = tmpconfig)

        self.pluginPrefs["pin"] = self.ecobee.pin
        if self.ecobee.authenticated:
            self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF] = self.ecobee.access_token
            self.pluginPrefs[AUTHORIZATION_CODE_PLUGIN_PREF] = self.ecobee.authorization_code
            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF] = self.ecobee.refresh_token
        if self.ecobee.refresh_token == '':
            self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF] = ''
            self.pluginPrefs[AUTHORIZATION_CODE_PLUGIN_PREF] = ''
            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF] = ''
            self.errorLog('Ecobee device requires authentication; open plugin configuration page for info')


    def __del__(self):
        indigo.PluginBase.__del__(self)

    def validatePrefsConfigUi(self, valuesDict):
        scaleInfo = valuesDict[TEMPERATURE_SCALE_PLUGIN_PREF]
        self._setTemperatureScale(scaleInfo[0])
        return True

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            try:
                self.logLevel = int(valuesDict[u"logLevel"])
            except:
                self.logLevel = logging.INFO
            self.log.setLevel(self.logLevel)
            logging.getLogger("indigo.ecobee.plugin").setLevel(self.logLevel)
            self.log.debug(u"logLevel = " + str(self.logLevel))

    ########################################

    #   constrain a setpoint the range
    #   based on temperature scale in use by the plugin
    def _constrainSetpoint(self, value):
        allowedRange = ALLOWED_RANGE[self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]]
        return clamp(value,allowedRange[0],allowedRange[1])

    #   convert value (in the plugin-defined scale)
    #   to Fahrenheit
    def _toFahrenheit(self,value):
        scale = self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]
        if scale == 'C':
            return (9 * value)/5 + 32
        elif scale == 'K':
            return (9 * value)/5 - 459.67
        elif scale == 'R':
            return 459.67
        return value

    def _setTemperatureScale(self, value):
        self.log.debug(u'setting temperature scale to %s' % value)
        EcobeeBase.temperatureFormatter = TEMP_FORMATTERS.get(value)


    def startup(self):
        indigo.server.log(u"Starting Ecobee")

        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "15"))
        self.logger.debug(u"updateFrequency = " + str(self.updateFrequency))
        self.next_update = time.time()

        self.next_refresh = time.time()
        
        self.triggers = {}
        self.authEventTriggered = False
        

    def shutdown(self):
        indigo.server.log(u"Stopping Ecobee")
        
        
    def runConcurrentThread(self):
        try:
            while True:

                if time.time() > self.next_update:
                    self.updateAllDevices()
                    self.next_update = time.time() + self.updateFrequency

                if time.time() > self.next_refresh:
                    self.ecobee.refresh_tokens()
                    self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF] = self.ecobee.access_token
                    self.pluginPrefs[AUTHORIZATION_CODE_PLUGIN_PREF] = self.ecobee.authorization_code
                    self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF] = self.ecobee.refresh_token
                    self.next_refresh = time.time() + REFRESH_INTERVAL

                if self.ecobee.authenticated:
                    self.ecobee.update()
                    
                    # We need to also re-save the authentication credentials now, since self.ecobee.update() may change them
                    self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF] = self.ecobee.access_token
                    self.pluginPrefs[AUTHORIZATION_CODE_PLUGIN_PREF] = self.ecobee.authorization_code
                    self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF] = self.ecobee.refresh_token
                    
                    # make sure we can trigger event next time we loose Authentication
                    self.authEventTriggered = False

                else:
                    if not self.authEventTriggered:
                        self.doTriggers()
                        self.authEventTriggered = True
                        

                self.sleep(15.0)

        except self.StopThread:
            pass

    def triggerStartProcessing(self, trigger):
        self.logger.debug("Adding Trigger %s (%d)" % (trigger.name, trigger.id))
        assert trigger.id not in self.triggers
        self.triggers[trigger.id] = trigger

    def triggerStopProcessing(self, trigger):
        self.logger.debug("Removing Trigger %s (%d)" % (trigger.name, trigger.id))
        assert trigger.id in self.triggers
        del self.triggers[trigger.id]

    def doTriggers(self):

        for triggerId, trigger in self.triggers.iteritems():

            if trigger.pluginTypeId == "authError":
                self.logger.debug("Executing Trigger %s (%d)" % (trigger.name, trigger.id))
                indigo.trigger.execute(trigger)
            else:
                self.logger.debug("Unknown Trigger Type %s (%d): %s" % (trigger.name, trigger.id, trigger.pluginTypeId))


    def request_pin(self, valuesDict = None):

        valuesDict[ACCESS_TOKEN_PLUGIN_PREF] = ''
        valuesDict[AUTHORIZATION_CODE_PLUGIN_PREF] = ''
        valuesDict[REFRESH_TOKEN_PLUGIN_PREF] = ''

        self.ecobee.request_pin()
        self.log.debug(u"received pin: %s" % self.ecobee.pin)
        valuesDict['pin'] = self.ecobee.pin
        return valuesDict

    def open_browser_to_ecobee(self, valuesDict = None):
        self.browserOpen("http://www.ecobee.com")

    # called from PluginConfig.xml
    def refresh_credentials(self, valuesDict = None):
        self.ecobee.request_tokens()
        self._get_keys_from_ecobee(valuesDict)
        if self.ecobee.authenticated:
            self.updateAllDevices()
        return valuesDict

    def get_thermostats(self, filter="", valuesDict=None, typeId="", targetId=0):
        return get_thermostats(self.ecobee)

    def get_remote_sensors(self, filter="", valuesDict=None, typeId="", targetId=0):
        return get_remote_sensors(self.ecobee)

    def _get_keys_from_ecobee(self, valuesDict):
        valuesDict[ACCESS_TOKEN_PLUGIN_PREF] = self.ecobee.access_token
        valuesDict[AUTHORIZATION_CODE_PLUGIN_PREF] = self.ecobee.authorization_code
        valuesDict[REFRESH_TOKEN_PLUGIN_PREF] = self.ecobee.refresh_token
        return valuesDict

    def deviceStartComm(self, dev):
        dev.stateListOrDisplayStateIdChanged() # in case any states added/removed after plugin upgrade

        if dev.model == 'Ecobee Remote Sensor':
            self.log.debug("deviceStartComm: creating EcobeeRemoteSensor")
            newDevice = EcobeeRemoteSensor(dev.pluginProps["address"], dev, self.ecobee)
            self.active_remote_sensors.append(newDevice)

            # set icon to 'temperature sensor'
            dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)

            indigo.server.log("added remote sensor %s" % dev.pluginProps["address"])

        elif dev.model == 'Ecobee Thermostat':
            # Add support for the thermostat's humidity sensor
            newProps = dev.pluginProps
            newProps["NumHumidityInputs"] = 1
            newProps["NumTemperatureInputs"] = 2
            # SHENANIGANS: the following property has to be set in order for us to report
            #   whether the thermostat is presently heating, cooling, etc.
            #   This was difficult to find.
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)
            newDevice = EcobeeThermostat(dev.pluginProps["address"], dev, self.ecobee)
            self.active_thermostats.append(newDevice)
            indigo.server.log("added thermostat %s" % dev.pluginProps["address"])

        elif dev.model == 'Ecobee Smart Thermostat':
            # Add support for the thermostat's humidity sensor
            newProps = dev.pluginProps
            newProps["NumHumidityInputs"] = 1
            # SHENANIGANS: the following property has to be set in order for us to report
            #   whether the thermostat is presently heating, cooling, etc.
            #   This was difficult to find.
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)
            newDevice = EcobeeSmartThermostat(dev.pluginProps["address"], dev, self.ecobee)
            self.active_smart_thermostats.append(newDevice)
            indigo.server.log("added smart thermostat %s" % dev.pluginProps["address"])

        # TODO: try to set initial name for new devices, as other plugins do.
        # However, this doesn't work yet. Sad clown.
        self.log.debug('device name: %s  ecobee name: %s' % (dev.name, newDevice.name))
        if dev.name == 'new device' and newDevice.name:
            dev.name = newDevice.name
            dev.replaceOnServer()
            self.log.debug('device name set to %s' % dev.name)

    def deviceStopComm(self, dev):
        if dev.model == 'Ecobee Remote Sensor':
            self.active_remote_sensors = [
                rs for rs in self.active_remote_sensors
                    if rs.address != dev.pluginProps["address"]
            ]
        elif dev.model == 'Ecobee Thermostat':
            self.active_thermostats = [
                t for t in self.active_thermostats
                    if t.address != dev.pluginProps["address"]
            ]
        elif dev.model == 'Ecobee Smart Thermostat':
            self.active_smart_thermostats = [
                st for st in self.active_smart_thermostats
                    if st.address != dev.pluginProps["address"]
            ]

    def updateAllDevices(self):
        for ers in self.active_remote_sensors:
            ers.updateServer()
        for t in self.active_thermostats:
            t.updateServer()
        for st in self.active_smart_thermostats:
            st.updateServer()

    ########################################
    # Thermostat Action callback
    ######################
    # Main thermostat action bottleneck called by Indigo Server.
    def actionControlThermostat(self, action, dev):
        ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            self.handleChangeHvacModeAction(dev, action.actionMode)

        ###### SET FAN MODE ######
        elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
            self.handleChangeFanModeAction(dev, action.actionMode, u"set fan hold", u"hvacFanIsOn")

        ###### SET COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetCoolSetpoint:
            newSetpoint = action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"change cool setpoint", u"setpointCool")

        ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            newSetpoint = action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"change heat setpoint", u"setpointHeat")

        ###### DECREASE/INCREASE COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
            newSetpoint = dev.coolSetpoint - action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"decrease cool setpoint", u"setpointCool")

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
            newSetpoint = dev.coolSetpoint + action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"increase cool setpoint", u"setpointCool")

        ###### DECREASE/INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint - action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"decrease heat setpoint", u"setpointHeat")

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint + action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"increase heat setpoint", u"setpointHeat")

        ###### REQUEST STATE UPDATES ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
         indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
         indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
           self.updateAllDevices()

        ###### UNTRAPPED CONDITIONS ######
        # Explicitly show when nothing matches, indicates errors and unimplemented actions instead of quietly swallowing them
        else:
            indigo.server.log(u"Error, received unimplemented action.thermostatAction:%s" % action.thermostatAction, isError=True)

    def climateListGenerator(self, filter, valuesDict, typeId, targetId):                                                                                                                 
        for t in self.active_thermostats:
            if t.dev.id == targetId:
                retList = get_climates(self.ecobee, t.dev.address)
        for t in self.active_smart_thermostats:
            if t.dev.id == targetId:
                retList = get_climates(self.ecobee, t.dev.address)
        return retList

    ########################################
    # Activate Comfort Setting callback
    ######################
    def actionActivateComfortSetting(self, action, dev):
        ###### ACTIVATE COMFORT SETTING ######
        climate = action.props.get("climate")

        sendSuccess = False
        if self.ecobee.set_climate_hold_id(dev.pluginProps["address"], climate) :
            sendSuccess = True;
            if sendSuccess:
                indigo.server.log(u"sent set_climate_hold to %s" % dev.address)
            else:
                indigo.server.log(u"Failed to send set_climate_hold to %s" % dev.address, isError=True)
        return sendSuccess

 
    ########################################
    # Resume Program callback
    ######################
    def actionResumeProgram(self, action, dev):
        resume_all = "false"
        if action.props.get("resume_all"):
            resume_all = "true"
        self.resumeProgram(dev, resume_all)

    # also called by other action functions
    
    def resumeProgram(self, dev, resume_all):
        sendSuccess = False
        if self.ecobee.resume_program_id(dev.pluginProps["address"], resume_all) :
            sendSuccess = True;
        if sendSuccess:
            indigo.server.log(u"sent resume_program to %s" % dev.address)
        else:
            indigo.server.log(u"Failed to send resume_program to %s" % dev.address, isError=True)
        return sendSuccess


        ######################
    # Process action request from Indigo Server to change main thermostat's main mode.
    def handleChangeHvacModeAction(self, dev, newHvacMode):
        hvac_mode = kHvacModeEnumToStrMap.get(newHvacMode, u"unknown")
        indigo.server.log(u"mode: %s --> set to: %s" % (newHvacMode, kHvacModeEnumToStrMap.get(newHvacMode)))
        indigo.server.log(u"address: %s set to: %s" % (int(dev.address), kHvacModeEnumToStrMap.get(newHvacMode)))

        sendSuccess = False

        if self.ecobee.set_hvac_mode_id(dev.pluginProps["address"], hvac_mode):
            sendSuccess = True

        if sendSuccess:
            indigo.server.log(u"sent \"%s\" mode change to %s" % (dev.name, hvac_mode))
            if "hvacOperationMode" in dev.states:
                dev.updateStateOnServer("hvacOperationMode", newHvacMode)
        else:
            indigo.server.log(u"send \"%s\" mode change to %s failed" % (dev.name, hvac_mode), isError=True)

    ######################
    # Process action request from Indigo Server to change a cool/heat setpoint.
    def handleChangeSetpointAction(self, dev, newSetpoint, logActionName, stateKey):
        oldNewSetpoint = newSetpoint
        self.log.debug('newSetpoint is {}'.format(newSetpoint))
        #   the newSetpoint is in whatever units configured in the pluginPrefs
        scale = self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]
        self.log.debug('scale in use is {}'.format(scale))
        #   enforce minima/maxima based on the scale in use by the plugin
        newSetpoint = self._constrainSetpoint(newSetpoint)
        #   API uses F scale
        newSetpoint = self._toFahrenheit(newSetpoint)
        sendSuccess = False
        #   Normalize units for consistent reporting
        reportedNewSetpoint = '{}{}'.format(oldNewSetpoint,scale)
        reportedHSP = '{}{}'.format(dev.heatSetpoint,scale)
        reportedCSP = '{}{}'.format(dev.heatSetpoint,scale)

        if stateKey == u"setpointCool":
            indigo.server.log('set cool to: {} and leave heat at: {}'.format(reportedNewSetpoint,reportedHSP))
            if self.ecobee.set_hold_temp_id(dev.address, newSetpoint, dev.heatSetpoint):
                sendSuccess = True

        elif stateKey == u"setpointHeat":
            indigo.server.log('set heat to: {} and leave cool at: {}'.format(reportedNewSetpoint,reportedCSP))
            if self.ecobee.set_hold_temp_id(dev.address, dev.coolSetpoint, newSetpoint):
                sendSuccess = True      # Set to False if it failed.

        if sendSuccess:
            indigo.server.log(u"sent \"%s\" %s to %.1f°" % (dev.name, logActionName, newSetpoint))
            # And then tell the Indigo Server to update the state.
            if stateKey in dev.states:
                dev.updateStateOnServer(stateKey, newSetpoint, uiValue="%.1f °F" % (newSetpoint))
        else:
            # Else log failure but do NOT update state on Indigo Server.
            indigo.server.log(u"send \"%s\" %s to %.1f° failed" % (dev.name, logActionName, newSetpoint), isError=True)

    ######################
    # Process action request from Indigo Server to change fan mode.
    def handleChangeFanModeAction(self, dev, requestedFanMode, logActionName, stateKey):
        newFanMode = kFanModeEnumToStrMap.get(requestedFanMode, u"auto")
        #   the scale is in whatever units configured in the pluginPrefs
        scale = self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]
        self.log.debug('scale in use is {0}'.format(scale))
        #   enforce minima/maxima based on the scale in use by the plugin
        sendSuccess = False
        #   Normalize units for consistent reporting
        reportedHSP = '{0}{1}'.format(dev.heatSetpoint,scale)
        reportedCSP = '{0}{1}'.format(dev.coolSetpoint,scale)

        if newFanMode == u"on":
            indigo.server.log('leave cool at: {0} and leave heat at: {1} and set fan to ON'.format(reportedCSP,reportedHSP))
            if self.ecobee.set_hold_temp_with_fan_id(dev.address, dev.coolSetpoint, dev.heatSetpoint):
                sendSuccess = True

        if newFanMode == u"auto":
            indigo.server.log('resume normal program to set fan to OFF')
            if self.resumeProgram(dev, "true"):
                sendSuccess = True

        if sendSuccess:
            indigo.server.log(u"sent \"%s\" %s to %s" % (dev.name, logActionName, newFanMode))
            # And then tell the Indigo Server to update the state.
            if stateKey in dev.states:
                dev.updateStateOnServer(stateKey, requestedFanMode, uiValue="True")
        else:
            # Else log failure but do NOT update state on Indigo Server.
            indigo.server.log(u"send \"%s\" %s to %s failed" % (dev.name, logActionName, newFanMode), isError=True)


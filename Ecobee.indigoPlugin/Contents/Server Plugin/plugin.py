#! /usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import requests
import json
import os
import time
import logging

from ecobee import EcobeeAccount
from ecobee_devices import EcobeeBase, EcobeeThermostat, EcobeeSmartThermostat, EcobeeRemoteSensor

import temperature_scale

REFRESH_TOKEN_PLUGIN_PREF='refreshToken'
ACCESS_TOKEN_PLUGIN_PREF='accessToken'
TEMPERATURE_SCALE_PLUGIN_PREF='temperatureScale'

API_KEY = "5FUDhiLW95utdvYo5eM806kp9B95dl2j"

TEMP_FORMATTERS = {
    'F': temperature_scale.Fahrenheit(),
    'C': temperature_scale.Celsius(),
    'K': temperature_scale.Kelvin(),
    'R': temperature_scale.Rankine()
}

kFanModeEnumToStrMap = {
    indigo.kFanMode.Auto            : u"auto",
    indigo.kFanMode.AlwaysOn        : u"on"
}

kHvacModeEnumToStrMap = {
    indigo.kHvacMode.Cool               : u"cool",
    indigo.kHvacMode.Heat               : u"heat",
    indigo.kHvacMode.HeatCool           : u"auto",
    indigo.kHvacMode.Off                : u"off",
    indigo.kHvacMode.ProgramHeat        : u"program heat",
    indigo.kHvacMode.ProgramCool        : u"program cool",
    indigo.kHvacMode.ProgramHeatCool    : u"program auto"
}

#   Plugin-enforced minimum and maximum setpoint ranges per temperature scale
ALLOWED_RANGE = {
    'F': (40,95),
    'C': (6,35),
    'K': (277,308),
    'R': (500,555)
}

REFRESH_INTERVAL = 45.0 * 60.0

class Plugin(indigo.PluginBase):

    def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs):
        indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
        pfmt = logging.Formatter('%(asctime)s.%(msecs)03d\t[%(levelname)8s] %(name)20s.%(funcName)-25s%(msg)s', datefmt='%Y-%m-%d %H:%M:%S')
        self.plugin_file_handler.setFormatter(pfmt)

        try:
            self.logLevel = int(self.pluginPrefs[u"logLevel"])
        except:
            self.logLevel = logging.INFO
        self.indigo_log_handler.setLevel(self.logLevel)
        self.logger.debug(u"logLevel = " + str(self.logLevel))


    def __del__(self):
        indigo.PluginBase.__del__(self)

    def startup(self):
        self.logger.info(u"Starting Ecobee")

        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "15")) *  60.0
        self.logger.debug(u"updateFrequency = " + str(self.updateFrequency))
        self.next_update = time.time()

        self.next_refresh = time.time()
        
        self.triggers = {}
        self.authenticated = False

        self.active_remote_sensors = {}
        self.active_thermostats = {}
        self.active_smart_thermostats = {}

        # create the Ecobee account object.  It will attempt to refresh the auth token.
        self.ecobee = EcobeeAccount(API_KEY, refresh_token = self.pluginPrefs.get(REFRESH_TOKEN_PLUGIN_PREF, None))
        if self.ecobee.authenticated:
            self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF] = self.ecobee.access_token
            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF] = self.ecobee.refresh_token
        else:
            self.logger.error('Ecobee plugin requires authentication; open plugin configuration page for info')

        if TEMPERATURE_SCALE_PLUGIN_PREF in self.pluginPrefs:
            self._setTemperatureScale(self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF][0])
        else:
            self._setTemperatureScale('F')


    def shutdown(self):
        self.logger.info(u"Stopping Ecobee")
        

    def validatePrefsConfigUi(self, valuesDict):
        self.logger.debug(u"validatePrefsConfigUi called")
        scaleInfo = valuesDict[TEMPERATURE_SCALE_PLUGIN_PREF]
        self._setTemperatureScale(scaleInfo[0])
        return True

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        if not userCancelled:
            try:
                self.logLevel = int(valuesDict[u"logLevel"])
            except:
                self.logLevel = logging.INFO
            self.indigo_log_handler.setLevel(self.logLevel)
            self.logger.debug(u"logLevel = " + str(self.logLevel))


    # Authentication Step 1, called from PluginConfig.xml
    def request_pin(self, valuesDict = None):
        return self.ecobee.request_pin(valuesDict)

    # Authentication Step 2, called from PluginConfig.xml
    def open_browser_to_ecobee(self, valuesDict = None):
        self.browserOpen("http://www.ecobee.com")

    # Authentication Step 3, called from PluginConfig.xml
    # called from PluginConfig.xml
    def get_tokens(self, valuesDict = None):
        return self.ecobee.get_tokens(valuesDict)

    ########################################
        
    def runConcurrentThread(self):
        try:
            while True:

                # Update from Ecobee servers as scheduled
                
                if time.time() > self.next_update:
                    if self.ecobee.authenticated:
                        self.ecobee.server_update()
                        self.updateAllDevices()
                        self.doTriggers()
                    else:
                        self.logger.warning("Not authenticated to Ecobee account, skipping update")
                    
                    self.next_update = time.time() + self.updateFrequency

                # Refresh the auth token as needed
                
                if time.time() > self.next_refresh:
                    if self.ecobee.authenticated:
                        self.ecobee.refresh_tokens()                    
                    self.next_refresh = time.time() + REFRESH_INTERVAL

                    
                self.sleep(60.0)

        except self.StopThread:
            pass

    ########################################

    def updateAllDevices(self):
        try:
            for devID, dev in self.active_remote_sensors.items():
                self.logger.debug(u"{}: Updating remote sensor".format(dev.name))
                dev.update()
        except:
            self.logger.exception(u"Error updating remote sensors")
        
        try:
            for devID, dev in self.active_thermostats.items():
                self.logger.debug(u"{}: Updating thermostat".format(dev.name))
                dev.update()
        except:
            self.logger.exception(u"Error updating thermostats")
        
        try:
            for devID, dev in self.active_smart_thermostats.items():
                self.logger.debug(u"{}: Updating smart thermostat".format(dev.name))
                dev.update()
        except:
            self.logger.exception(u"Error updating smart thermostats")
        
    ########################################

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

    ########################################
    #
    # callbacks from device creation UI
    #
    
    def get_thermostat_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        return [
            (th.get('identifier'), th.get('name'))
            for th in self.ecobee.get_thermostats()
        ]


    def get_remote_sensor_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        return [
            (rs.get('code'), rs.get('name'))
            for rs in self.ecobee.get_remote_sensors()
        ]


    ########################################

    def deviceStartComm(self, dev):
        dev.stateListOrDisplayStateIdChanged() # in case any states added/removed after plugin upgrade

        if dev.deviceTypeId == 'ecobeeAccount':
            pass

        elif dev.deviceTypeId == 'EcobeeRemoteSensor':

            dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)
            self.active_remote_sensors[dev.id] = EcobeeRemoteSensor(dev, self.ecobee)
            self.logger.debug("Starting {} {} ({})".format(dev.deviceTypeId, dev.name, dev.pluginProps["address"]))

        elif dev.deviceTypeId == 'EcobeeThermostat':

            newProps = dev.pluginProps
            newProps["NumHumidityInputs"] = 1
            newProps["NumTemperatureInputs"] = 2
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)

            self.active_thermostats[dev.id] = EcobeeThermostat(dev, self.ecobee)
            self.logger.info("Starting {} {} ({})".format(dev.deviceTypeId, dev.name, dev.pluginProps["address"]))

        elif dev.deviceTypeId == 'EcobeeSmartThermostat':

            newProps = dev.pluginProps
            newProps["NumHumidityInputs"] = 1
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)

            self.active_smart_thermostats[dev.id] = EcobeeSmartThermostat(dev, self.ecobee)
            self.logger.info("Starting {} {} ({})".format(dev.deviceTypeId, dev.name, dev.pluginProps["address"]))

        else:
            self.logger.error("Unknown Ecobee device type: {}".format(dev.deviceTypeId))


    def deviceStopComm(self, dev):
        if dev.deviceTypeId == 'EcobeeRemoteSensor':
            self.logger.debug("Removing Device %s (%d) from EcobeeRemoteSensor list" % (dev.name, dev.id))
            assert dev.id in self.active_remote_sensors
            del self.active_remote_sensors[dev.id]
 
        elif dev.deviceTypeId == 'EcobeeThermostat':
            self.logger.debug("Removing Device %s (%d) from EcobeeThermostat list" % (dev.name, dev.id))
            assert dev.id in self.active_thermostats
            del self.active_thermostats[dev.id]

        elif dev.deviceTypeId == 'EcobeeSmartThermostat':
            self.logger.debug("Removing Device %s (%d) from EcobeeSmartThermostat list" % (dev.name, dev.id))
            assert dev.id in self.active_smart_thermostats
            del self.active_smart_thermostats[dev.id]


    ########################################
    # Thermostat Action callbacks
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
            self.logger.info(u"Error, received unimplemented action.thermostatAction:%s" % action.thermostatAction, isError=True)

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
        if self.set_climate_hold(dev.pluginProps["address"], climate) :
            sendSuccess = True;
            if sendSuccess:
                self.logger.info(u"sent set_climate_hold to %s" % dev.address)
            else:
                self.logger.info(u"Failed to send set_climate_hold to %s" % dev.address, isError=True)
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
        if self.resume_program(dev.pluginProps["address"], resume_all) :
            sendSuccess = True;
        if sendSuccess:
            self.logger.info(u"sent resume_program to %s" % dev.address)
        else:
            self.logger.info(u"Failed to send resume_program to %s" % dev.address, isError=True)
        return sendSuccess


        ######################
    # Process action request from Indigo Server to change main thermostat's main mode.
    def handleChangeHvacModeAction(self, dev, newHvacMode):
        hvac_mode = kHvacModeEnumToStrMap.get(newHvacMode, u"unknown")
        self.logger.info(u"mode: %s --> set to: %s" % (newHvacMode, kHvacModeEnumToStrMap.get(newHvacMode)))
        self.logger.info(u"address: %s set to: %s" % (int(dev.address), kHvacModeEnumToStrMap.get(newHvacMode)))

        sendSuccess = False

        if self.set_hvac_mode(dev.pluginProps["address"], hvac_mode):
            sendSuccess = True

        if sendSuccess:
            self.logger.info(u"sent \"%s\" mode change to %s" % (dev.name, hvac_mode))
            if "hvacOperationMode" in dev.states:
                dev.updateStateOnServer("hvacOperationMode", newHvacMode)
        else:
            self.logger.info(u"send \"%s\" mode change to %s failed" % (dev.name, hvac_mode), isError=True)

    ######################
    # Process action request from Indigo Server to change a cool/heat setpoint.
    
    def handleChangeSetpointAction(self, dev, newSetpoint, logActionName, stateKey):
        oldNewSetpoint = newSetpoint
        self.logger.debug('newSetpoint is {}'.format(newSetpoint))
        #   the newSetpoint is in whatever units configured in the pluginPrefs
        scale = self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]
        self.logger.debug('scale in use is {}'.format(scale))
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
            self.logger.info('set cool to: {} and leave heat at: {}'.format(reportedNewSetpoint,reportedHSP))
            if self.set_hold_temp(dev.address, newSetpoint, dev.heatSetpoint):
                sendSuccess = True

        elif stateKey == u"setpointHeat":
            self.logger.info('set heat to: {} and leave cool at: {}'.format(reportedNewSetpoint,reportedCSP))
            if self.set_hold_temp(dev.address, dev.coolSetpoint, newSetpoint):
                sendSuccess = True      # Set to False if it failed.

        if sendSuccess:
            self.logger.info(u"sent \"%s\" %s to %.1f°" % (dev.name, logActionName, newSetpoint))
            # And then tell the Indigo Server to update the state.
            if stateKey in dev.states:
                dev.updateStateOnServer(stateKey, newSetpoint, uiValue="%.1f °F" % (newSetpoint))
        else:
            # Else log failure but do NOT update state on Indigo Server.
            self.logger.info(u"send \"%s\" %s to %.1f° failed" % (dev.name, logActionName, newSetpoint), isError=True)

    ######################
    # Process action request from Indigo Server to change fan mode.
    
    def handleChangeFanModeAction(self, dev, requestedFanMode, logActionName, stateKey):
        newFanMode = kFanModeEnumToStrMap.get(requestedFanMode, u"auto")
        #   the scale is in whatever units configured in the pluginPrefs
        scale = self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]
        self.logger.debug('scale in use is {0}'.format(scale))
        #   enforce minima/maxima based on the scale in use by the plugin
        sendSuccess = False
        #   Normalize units for consistent reporting
        reportedHSP = '{0}{1}'.format(dev.heatSetpoint,scale)
        reportedCSP = '{0}{1}'.format(dev.coolSetpoint,scale)

        if newFanMode == u"on":
            self.logger.info('leave cool at: {0} and leave heat at: {1} and set fan to ON'.format(reportedCSP,reportedHSP))
            if self.set_hold_temp_with_fan(dev.address, dev.coolSetpoint, dev.heatSetpoint):
                sendSuccess = True

        if newFanMode == u"auto":
            self.logger.info('resume normal program to set fan to OFF')
            if self.resumeProgram(dev, "true"):
                sendSuccess = True

        if sendSuccess:
            self.logger.info(u"sent \"%s\" %s to %s" % (dev.name, logActionName, newFanMode))
            # And then tell the Indigo Server to update the state.
            if stateKey in dev.states:
                dev.updateStateOnServer(stateKey, requestedFanMode, uiValue="True")
        else:
            # Else log failure but do NOT update state on Indigo Server.
            self.logger.info(u"send \"%s\" %s to %s failed" % (dev.name, logActionName, newFanMode), isError=True)

    #   constrain a setpoint the range
    #   based on temperature scale in use by the plugin
    def _constrainSetpoint(self, value):
        allowedRange = ALLOWED_RANGE[self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF]]
        return min(max(value, allowedRange[0]), allowedRange[1])

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
        self.logger.debug(u'setting temperature scale to %s' % value)
        EcobeeBase.temperatureFormatter = TEMP_FORMATTERS.get(value)


#! /usr/bin/env python
# -*- coding: utf-8 -*-

import time
import logging

from ecobee import EcobeeAccount, EcobeeBase, EcobeeThermostat, EcobeeSmartThermostat, EcobeeRemoteSensor

import temperature_scale

REFRESH_TOKEN_PLUGIN_PREF='refreshToken-'
ACCESS_TOKEN_PLUGIN_PREF='accessToken-'
TEMPERATURE_SCALE_PLUGIN_PREF='temperatureScale'

API_KEY = "rzQZSWoFdELWHGfATFJEzrfYs1rccT9h"

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

kHvacModeEnumToStrMap = {
    indigo.kHvacMode.Cool               : u"cool",
    indigo.kHvacMode.Heat               : u"heat",
    indigo.kHvacMode.HeatCool           : u"auto",
    indigo.kHvacMode.Off                : u"off",
    indigo.kHvacMode.ProgramHeat        : u"program heat",
    indigo.kHvacMode.ProgramCool        : u"program cool",
    indigo.kHvacMode.ProgramHeatCool    : u"program auto"
}

kFanModeEnumToStrMap = {
    indigo.kFanMode.Auto            : u"auto",
    indigo.kFanMode.AlwaysOn        : u"on"
}

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
        self.next_update = time.time() + self.updateFrequency
        
        self.triggers = {}
        self.active_devices = {}
        self.ecobee_accounts = {}
        self.update_needed = False
        
        if TEMPERATURE_SCALE_PLUGIN_PREF in self.pluginPrefs:
            self._setTemperatureScale(self.pluginPrefs[TEMPERATURE_SCALE_PLUGIN_PREF][0])
        else:
            self._setTemperatureScale('F')


    def shutdown(self):
        self.logger.info(u"Stopping Ecobee")
        

    def validatePrefsConfigUi(self, valuesDict):
        self.logger.debug(u"validatePrefsConfigUi called")
        errorDict = indigo.Dict()

        updateFrequency = int(valuesDict['updateFrequency'])
        if (updateFrequency < 5) or (updateFrequency > 60):
            errorDict['updateFrequency'] = u"Update frequency is invalid - enter a valid number (between 5 and 60)"

        if len(errorDict) > 0:
            return (False, valuesDict, errorDict)

        return True

    def closedPrefsConfigUi(self, valuesDict, userCancelled):
        self.logger.debug(u"closedPrefsConfigUi called")
        if not userCancelled:
            try:
                self.logLevel = int(valuesDict[u"logLevel"])
            except:
                self.logLevel = logging.INFO
            self.indigo_log_handler.setLevel(self.logLevel)
            self.logger.debug(u"logLevel = " + str(self.logLevel))

            self.updateFrequency = float(valuesDict['updateFrequency']) * 60.0
            self.logger.debug(u"updateFrequency = " + str(self.updateFrequency))
            self.next_update = time.time()

            scaleInfo = valuesDict[TEMPERATURE_SCALE_PLUGIN_PREF]
            self._setTemperatureScale(scaleInfo[0])
        

    ########################################
        
    def runConcurrentThread(self):
        try:
            while True:
                
                if (time.time() > self.next_update) or self.update_needed:
                
                    # update from Ecobee servers
                    
                    for account in self.ecobee_accounts.values():
                        if account.authenticated:
                            account.server_update()
                    
                    # now update all the Indigo devices         
                    
                    for dev in self.active_devices.values():
                        dev.update()
                    
                    self.next_update = time.time() + self.updateFrequency
                    self.update_needed = False

                # Refresh the auth tokens as needed.  Refresh interval for each account is calculated during the refresh
                
                for accountID, account in self.ecobee_accounts.items():
                    if time.time() > account.next_refresh:
                        if account.authenticated:
                            account.do_token_refresh()                    
                            self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF + str(accountID)] = account.access_token
                            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(accountID)] = account.refresh_token

                    
                self.sleep(1.0)

        except self.StopThread:
            pass

                
    ########################################

    def triggerStartProcessing(self, trigger):
        self.logger.debug("Adding Trigger %s (%d)" % (trigger.name, trigger.id))
        assert trigger.id not in self.triggers
        self.triggers[trigger.id] = trigger

    def triggerStopProcessing(self, trigger):
        self.logger.debug("Removing Trigger %s (%d)" % (trigger.name, trigger.id))
        assert trigger.id in self.triggers 
        del self.triggers[trigger.id]

    def doAuthErrorTriggers(self):

        for triggerId, trigger in self.triggers.iteritems():

            if trigger.pluginTypeId == "authError":
                self.logger.debug("Executing Trigger %s (%d)" % (trigger.name, trigger.id))
                indigo.trigger.execute(trigger)

    ########################################
    #
    # callbacks from device creation UI
    #
    ########################################

    def get_account_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_account_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))
        return [
            (account.dev.id, indigo.devices[account.dev.id].name)
            for account in self.ecobee_accounts.values()
        ]
    

    def get_thermostat_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_thermostat_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))

        if "account" not in valuesDict:
            return []
            
        try:
            ecobee = self.ecobee_accounts[int(valuesDict["account"])]
        except:
            self.logger.error("get_thermostat_list: error accessing ecobee account")
            return []
            
        all_stats = [
            (th.get('identifier'), th.get('name'))
            for th in ecobee.get_thermostats()
        ]
        self.logger.debug("get_thermostat_list: all_stats = {}".format(all_stats))
        
        active_stats =  [
            (indigo.devices[dev].pluginProps["address"])
            for dev in self.active_devices
        ]
        self.logger.debug("get_thermostat_list: active_stats = {}".format(active_stats))

        filtered_stats =[]
        for iden, name in all_stats:
            if iden not in active_stats:
                filtered_stats.append((iden, name))
        
        if targetId:
            try:
                dev = indigo.devices[targetId]
                filtered_stats.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass
                
        self.logger.debug("get_thermostat_list: filtered_stats = {}".format(filtered_stats))
        return filtered_stats


    def get_remote_sensor_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.debug("get_remote_sensor_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))

        if "account" not in valuesDict:
            return []
            
        try:
            ecobee = self.ecobee_accounts[int(valuesDict["account"])]
        except:
            self.logger.debug("get_remote_sensor_list: error accessing ecobee account")

        all_sensors = [
            (rs.get('code'), rs.get('name'))
            for rs in ecobee.get_remote_sensors()
        ]
        self.logger.debug("get_remote_sensor_list: all_sensors = {}".format(all_sensors))

        self.logger.debug("get_remote_sensor_list: active_devices = {}".format(self.active_devices))

        active_sensors =  [
            (indigo.devices[dev].pluginProps["address"])
            for dev in self.active_devices
        ]
        self.logger.debug("get_remote_sensor_list: active_sensors = {}".format(active_sensors))

        filtered_sensors =[]
        for iden, name in all_sensors:
            if iden not in active_sensors:
                filtered_sensors.append((iden, name))

        if targetId:
            try:
                dev = indigo.devices[targetId]
                filtered_sensors.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass

        self.logger.debug("get_remote_sensor_list: filtered_sensors = {}".format(filtered_sensors))
        return filtered_sensors

     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict


    def closedDeviceConfigUi(self, valuesDict, userCancelled, typeId, devId):
        self.logger.debug("closedDeviceConfigUi: userCancelled = {}, typeId = {}, devId = {}, valuesDict = {}".format(userCancelled, typeId, devId, valuesDict))
        return
        

    ########################################

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        valuesDict = indigo.Dict(pluginProps)
        errorsDict = indigo.Dict()
        self.logger.debug("getDeviceConfigUiValues, typeID = {}, valuesDict = {}".format(typeId, valuesDict))

        if len(valuesDict) == 0:
            self.logger.debug("getDeviceConfigUiValues: no values")
        else:
            self.logger.debug("getDeviceConfigUiValues: no change, already populated")

        return (valuesDict, errorsDict)


    def deviceStartComm(self, dev):

        self.logger.info(u"{}: Starting {} Device {}".format(dev.name, dev.deviceTypeId, dev.id))

        dev.stateListOrDisplayStateIdChanged() # in case any states added/removed after plugin upgrade

        if dev.deviceTypeId == 'EcobeeAccount':
        
            # create the Ecobee account object.  It will attempt to refresh the auth token.
            
            ecobeeAccount = EcobeeAccount(dev, API_KEY, refresh_token = self.pluginPrefs.get(REFRESH_TOKEN_PLUGIN_PREF + str(dev.id), None))

            self.ecobee_accounts[dev.id] = ecobeeAccount
            
            dev.updateStateOnServer(key="authenticated", value=ecobeeAccount.authenticated)

            if ecobeeAccount.authenticated:
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
                self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF + str(dev.id)] = ecobeeAccount.access_token
                self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(dev.id)] = ecobeeAccount.refresh_token
            else:
                dev.updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
            
        elif dev.deviceTypeId == 'EcobeeThermostat':

            newProps = dev.pluginProps
            newProps["NumHumidityInputs"] = 1
            newProps["NumTemperatureInputs"] = 2
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)

            self.active_devices[dev.id] = EcobeeThermostat(dev)
            self.update_needed = True
            
        elif dev.deviceTypeId == 'EcobeeSmartThermostat':

            newProps = dev.pluginProps
            newProps["NumHumidityInputs"] = 1
            newProps["ShowCoolHeatEquipmentStateUI"] = True
            dev.replacePluginPropsOnServer(newProps)

            self.active_devices[dev.id] = EcobeeSmartThermostat(dev)
            self.update_needed = True

        elif dev.deviceTypeId == 'EcobeeRemoteSensor':

            newProps = dev.pluginProps
            newProps["AllowSensorValueChange"]  = False
            newProps["AllowOnStateChange"]      = False
            dev.replacePluginPropsOnServer(newProps)

            dev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)

            self.active_devices[dev.id] = EcobeeRemoteSensor(dev)
            self.update_needed = True

        else:
            self.logger.error(u"{}: deviceStartComm error, unknown device type: {}".format(dev.name, dev.deviceTypeId))


    def deviceStopComm(self, dev):

        self.logger.info(u"{}: Stopping {} Device {}".format( dev.name, dev.deviceTypeId, dev.id))

        if dev.deviceTypeId in ['EcobeeRemoteSensor', 'EcobeeThermostat', 'EcobeeSmartThermostat']:
            assert dev.id in self.active_devices
            del self.active_devices[dev.id]
 
        elif dev.deviceTypeId == 'EcobeeAccount':
            assert dev.id in self.ecobee_accounts
            del self.ecobee_accounts[dev.id]
            
        else:
            self.logger.error(u"{}: deviceStopComm error, unknown device type: {}".format(dev.name, dev.deviceTypeId))
        
         
#    Authentication Step 1, called from Devices.xml

    def request_pin(self, valuesDict, typeId, devId):
        self.temp_ecobeeAccount = EcobeeAccount(None, API_KEY, None)
        pin = self.temp_ecobeeAccount.request_pin()
        if pin:
            valuesDict["pin"] = pin
            valuesDict["authStatus"] = "PIN Request OK"
        else:
            valuesDict["authStatus"] = "PIN Request Failed"
        return valuesDict

#    Authentication Step 2, called from Devices.xml

    def open_browser_to_ecobee(self, valuesDict, typeId, devId):
        self.browserOpen("https://www.ecobee.com/consumerportal/")

#    Authentication Step 3, called from Devices.xml

    def get_tokens(self, valuesDict, typeId, devId):
        valuesDict["pin"] = ''
        self.temp_ecobeeAccount.get_tokens()
        if self.temp_ecobeeAccount.authenticated:
            valuesDict["authStatus"] = "Authenticated"
            self.pluginPrefs[ACCESS_TOKEN_PLUGIN_PREF + str(devId)] = self.temp_ecobeeAccount.access_token
            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(devId)] = self.temp_ecobeeAccount.refresh_token
        else:
            valuesDict["authStatus"] = "Token Request Failed"
        return valuesDict


    ########################################
    # Thermostat Action callbacks
    ########################################
    
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
            self.handleChangeSetpointAction(dev, newSetpoint, u"setpointCool")

        ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            newSetpoint = action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"setpointHeat")

        ###### DECREASE/INCREASE COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
            newSetpoint = dev.coolSetpoint - action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"setpointCool")

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
            newSetpoint = dev.coolSetpoint + action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"setpointCool")

        ###### DECREASE/INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint - action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"setpointHeat")

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            newSetpoint = dev.heatSetpoint + action.actionValue
            self.handleChangeSetpointAction(dev, newSetpoint, u"setpointHeat")

        ###### REQUEST STATE UPDATES ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
         indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
         indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
            self.active_devices[dev.id].update()

        ###### UNTRAPPED CONDITIONS ######
        # Explicitly show when nothing matches, indicates errors and unimplemented actions instead of quietly swallowing them
        else:
            self.logger.warning(u"{}: Unimplemented action.thermostatAction: {}".format(dev.name, action.thermostatAction))

    def actionControlUniversal(self, action, dev):
        if action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self.active_devices[dev.id].update()
        else:
            self.logger.warning(u"{}: Unimplemented action.deviceAction: {}".format(dev.name, action.deviceAction))


    ########################################
    # Activate Comfort Setting callback
    ########################################
    
    def actionActivateComfortSetting(self, action, dev):
        self.logger.debug(u"{}: actionActivateComfortSetting".format(dev.name))
        climate = action.props.get("climate")
        self.active_devices[dev.id].set_climate_hold(climate)

    def climateListGenerator(self, filter, valuesDict, typeId, targetId):                                                                                                                 
        self.logger.debug(u"climateListGenerator: typeId = {}, targetId = {}".format(typeId, targetId))
        return self.active_devices[targetId].get_climates()

 
    ########################################
    # Resume Program callbacks
    ########################################
    
    def menuResumeProgram(self):
        self.logger.debug(u"menuResumeProgram")
        for devId, thermostat in self.active_devices.items():
            if indigo.devices[devId].deviceTypeId in ['EcobeeThermostat', 'EcobeeSmartThermostat']:
                thermostat.resume_program()

    def actionResumeProgram(self, action, dev):
        self.logger.debug(u"{}: actionResumeProgram".format(dev.name))
        self.active_devices[dev.id].resume_program()

    def actionResumeAllPrograms(self, action, dev):
        self.logger.debug(u"actionResumeAllPrograms")
        for devId, thermostat in self.active_devices.items():
            if indigo.devices[devId].deviceTypeId in ['EcobeeThermostat', 'EcobeeSmartThermostat']:
                thermostat.resume_program()
    

    ########################################
    # Process action request from Indigo Server to change main thermostat's main mode.
    ########################################

    def handleChangeHvacModeAction(self, dev, newHvacMode):
        hvac_mode = kHvacModeEnumToStrMap.get(newHvacMode, u"unknown")
        self.logger.debug(u"{} ({}): Mode set to: {}".format(dev.name, dev.address, newHvacMode))

        self.active_devices[dev.id].set_hvac_mode(hvac_mode)
        if "hvacOperationMode" in dev.states:
            dev.updateStateOnServer("hvacOperationMode", newHvacMode)

    ########################################
    # Process action request from Indigo Server to change a cool/heat setpoint.
    ########################################
    
    def handleChangeSetpointAction(self, dev, newSetpoint, stateKey):

        #   enforce minima/maxima based on the scale in use by the plugin
        newSetpoint = self._constrainSetpoint(newSetpoint)

        #   API uses F scale
        newSetpoint = self._toFahrenheit(newSetpoint)

        if stateKey == u"setpointCool":
            self.logger.info(u'{}: set cool to: {} and leave heat at: {}'.format(dev.name, newSetpoint, dev.heatSetpoint))
            self.active_devices[dev.id].set_hold_temp(newSetpoint, dev.heatSetpoint)

        elif stateKey == u"setpointHeat":
            self.logger.info(u'{}: set heat to: {} and leave cool at: {}'.format(dev.name, newSetpoint,dev.coolSetpoint))
            self.active_devices[dev.id].set_hold_temp(dev.coolSetpoint, newSetpoint)

        else:
            self.logger.error(u'{}: handleChangeSetpointAction Invalid operation - {}'.format(dev.name, stateKey))
        
        if stateKey in dev.states:
            dev.updateStateOnServer(stateKey, newSetpoint, uiValue="%.1f °F" % (newSetpoint))


    ########################################
    # Process action request from Indigo Server to change fan mode.
    ########################################
    
    def handleChangeFanModeAction(self, dev, requestedFanMode, stateKey):
       
        newFanMode = kFanModeEnumToStrMap.get(requestedFanMode, u"auto")
        
        if newFanMode == u"on":
            self.logger.info(u'{}: set fan to ON, leave cool at {} and heat at {}'.format(dev.name, dev.coolSetpoint,dev.heatSetpoint))
            self.active_devices[dev.id].set_hold_temp_with_fan(dev.coolSetpoint, dev.heatSetpoint)

        if newFanMode == u"auto":
            self.logger.info(u'{}: resume normal program to set fan to Auto'.format(dev.name))
            self.active_devices[dev.id].resumeProgram()

        if stateKey in dev.states:
            dev.updateStateOnServer(stateKey, requestedFanMode, uiValue="True")


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


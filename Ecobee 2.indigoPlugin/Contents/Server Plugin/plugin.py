#! /usr/bin/env python
# -*- coding: utf-8 -*-

import time
import logging
import json
import platform

from ecobee import EcobeeAccount, EcobeeThermostat

import temperature_scale

REFRESH_TOKEN_PLUGIN_PREF='refreshToken-'
TEMPERATURE_SCALE_PLUGIN_PREF='temperatureScale'

API_KEY = "opSMO6RtoUlhoAtlQehNZdaOZ6EQBO6Q"

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

kCurDevVersCount = 0        # current version of plugin devices

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


    def startup(self):
        self.logger.info(u"Starting Ecobee")
       
        macOS = platform.mac_ver()[0]
        self.logger.debug(u"macOS version = {}".format(macOS))
        if int(macOS[3:5]) < 13:
            self.logger.error(u"Unsupported macOS version! {}".format(macOS))
        

        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "15")) *  60.0
        self.logger.debug(u"updateFrequency = " + str(self.updateFrequency))
        self.next_update = time.time() + self.updateFrequency
        
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
                            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(accountID)] = account.refresh_token
                            self.savePluginPrefs()

                self.sleep(2.0)

        except self.StopThread:
            pass

                
    ########################################
    #
    # callbacks from device creation UI
    #
    ########################################

    def get_account_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug("get_account_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))
        return [
            (account.dev.id, indigo.devices[account.dev.id].name)
            for account in self.ecobee_accounts.values()
        ]
    

    def get_thermostat_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug("get_thermostat_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))

        if "account" not in valuesDict:
            return []
            
        try:
            ecobee = self.ecobee_accounts[int(valuesDict["account"])]
        except:
            self.logger.error("get_thermostat_list: error accessing ecobee account")
            return []
            
        active_stats =  [
            (indigo.devices[dev].pluginProps["address"])
            for dev in self.active_devices
        ]
        self.logger.threaddebug("get_thermostat_list: active_stats = {}".format(active_stats))

        filtered_stats =[]
        for iden, therm in ecobee.thermostats.items():
            if iden not in active_stats:
                filtered_stats.append((iden, therm["name"]))
        
        if targetId:
            try:
                dev = indigo.devices[targetId]
                filtered_stats.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass
                
        self.logger.threaddebug("get_thermostat_list: filtered_stats = {}".format(filtered_stats))
        return filtered_stats     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict


    ########################################

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        valuesDict = indigo.Dict(pluginProps)
        errorsDict = indigo.Dict()
        self.logger.threaddebug("getDeviceConfigUiValues, typeID = {}, valuesDict = {}".format(typeId, valuesDict))
        return (valuesDict, errorsDict)

    def getDeviceFactoryUiValues(self, devIdList):
        self.logger.threaddebug("getDeviceFactoryUiValues, devIdList = {}".format(devIdList))
        valuesDict = indigo.Dict()
        errorMsgDict = indigo.Dict()

        # change default to creating Thermostats if there's at least one account defined
        
        if len(self.ecobee_accounts) > 0:
            valuesDict["deviceType"] = "EcobeeThermostat"
            valuesDict["account"] = self.ecobee_accounts[self.ecobee_accounts.keys()[0]].dev.id
            
        return (valuesDict, errorMsgDict)

    def validateDeviceFactoryUi(self, valuesDict, devIdList):
        self.logger.threaddebug("validateDeviceFactoryUi, valuesDict = {}, devIdList = {}".format(valuesDict, devIdList))
        errorsDict = indigo.Dict()
        return (True, valuesDict, errorsDict)

    def closedDeviceFactoryUi(self, valuesDict, userCancelled, devIdList):
        self.logger.threaddebug("closedDeviceFactoryUi, userCancelled = {}, valuesDict = {}, devIdList = {}".format(userCancelled, valuesDict, devIdList))
        
        if userCancelled:
            return
            
        if valuesDict["deviceType"] == "EcobeeAccount":
        
            newdev = indigo.device.create(indigo.kProtocol.Plugin, deviceTypeId="EcobeeAccount")
            newdev.model = "Ecobee Account"
            newdev.replaceOnServer()

        elif valuesDict["deviceType"] == "EcobeeThermostat":
        
            newdev = indigo.device.create(indigo.kProtocol.Plugin, deviceTypeId="EcobeeThermostat")
            newdev.model = "Ecobee Thermostat"
            newdev.replaceOnServer()

            newProps = newdev.pluginProps
            newProps["address"] = valuesDict["address"]
            newProps["account"] = valuesDict["account"]
            newdev.replacePluginPropsOnServer(newProps)                

        return

    ######################
    #
    #  Subclass this if you dynamically need to change the device states list provided based on specific device instance data.
      
    def getDeviceStateList(self, dev):
        
        stateList = indigo.PluginBase.getDeviceStateList(self, dev)
        device_type = dev.pluginProps.get("device_type", None)
        
        self.logger.debug("getDeviceStateList, typeID = {}, model = {}, device_type = {}".format(dev.deviceTypeId, dev.subModel, device_type))
                        
        if device_type in ['athenaSmart', 'corSmart']:

            stateList.append({  "Disabled"     : False, 
                                "Key"          : "device_type", 
                                "StateLabel"   : "Model",   
                                "TriggerLabel" : "Model",   
                                "Type"         : 150 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "climate",     
                                "StateLabel"   : "Climate", 
                                "TriggerLabel" : "Climate", 
                                "Type"         : 150 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "equipmentStatus",     
                                "StateLabel"   : "Status", 
                                "TriggerLabel" : "Status", 
                                "Type"         : 150 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "occupied", 
                                "StateLabel"   : "Occupied (yes or no)",   
                                "TriggerLabel" : "Occupied",   
                                "Type"         : 52 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "autoAway", 
                                "StateLabel"   : "Auto-Away (yes or no)",   
                                "TriggerLabel" : "Auto-Away",   
                                "Type"         : 52 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "autoHome", 
                                "StateLabel"   : "Auto-Home (yes or no)",   
                                "TriggerLabel" : "Auto-Home",   
                                "Type"         : 52 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "fanMinOnTime", 
                                "StateLabel"   : "Minimum fan time",   
                                "TriggerLabel" : "Minimum fan time",   
                                "Type"         : 100 })
        

        elif device_type == 'idtSmart':

            stateList.append({  "Disabled"     : False, 
                                "Key"          : "device_type", 
                                "StateLabel"   : "Model",   
                                "TriggerLabel" : "Model",   
                                "Type"         : 150 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "climate",     
                                "StateLabel"   : "Climate", 
                                "TriggerLabel" : "Climate", 
                                "Type"         : 150 })
            stateList.append({  "Disabled"     : False, 
                                "Key"          : "equipmentStatus",     
                                "StateLabel"   : "Status", 
                                "TriggerLabel" : "Status", 
                                "Type"         : 150 })
        
        self.logger.threaddebug("getDeviceStateList, returning state list = {}".format(stateList))        
        return stateList

    def deviceStartComm(self, dev):

        self.logger.info(u"{}: Starting {} Device {}".format(dev.name, dev.deviceTypeId, dev.id))
        self.logger.threaddebug(u"{}: Device pluginProps = {}".format(dev.name,  dev.pluginProps))

        instanceVers = int(dev.pluginProps.get('devVersCount', 0))
        if instanceVers == kCurDevVersCount:
            self.logger.threaddebug(u"%s: Device is current version: %d" % (dev.name ,instanceVers))
        elif instanceVers < kCurDevVersCount:
            newProps = dev.pluginProps
            newProps["devVersCount"] = kCurDevVersCount
            dev.replacePluginPropsOnServer(newProps)
            self.logger.debug(u"%s: Updated device version: %d -> %d" % (dev.name,  instanceVers, kCurDevVersCount))
        else:
            self.logger.warning(u"%s: Invalid device version: %d" % (dev.name, instanceVers))
        
        dev.stateListOrDisplayStateIdChanged()

        if dev.deviceTypeId == 'EcobeeAccount':
        
            # create the Ecobee account object.  It will attempt to refresh the auth token.
            
            ecobeeAccount = EcobeeAccount(dev, API_KEY, refresh_token = self.pluginPrefs.get(REFRESH_TOKEN_PLUGIN_PREF + str(dev.id), None))

            self.ecobee_accounts[dev.id] = ecobeeAccount
            
            dev.updateStateOnServer(key="authenticated", value=ecobeeAccount.authenticated)

            if ecobeeAccount.authenticated:
                self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(dev.id)] = ecobeeAccount.refresh_token
                self.savePluginPrefs()
                            
        elif dev.deviceTypeId == 'EcobeeThermostat':

            self.active_devices[dev.id] = EcobeeThermostat(dev)
            self.update_needed = True
            


    def deviceStopComm(self, dev):

        self.logger.info(u"{}: Stopping {} Device {}".format( dev.name, dev.deviceTypeId, dev.id))

        if dev.deviceTypeId == 'EcobeeThermostat':
            if dev.id in self.active_devices:
                del self.active_devices[dev.id]
 
        elif dev.deviceTypeId == 'EcobeeAccount':
            if dev.id in self.ecobee_accounts:
                del self.ecobee_accounts[dev.id]
            
                     
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
            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(devId)] = self.temp_ecobeeAccount.refresh_token
            self.savePluginPrefs()
        else:
            valuesDict["authStatus"] = "Token Request Failed"
        return valuesDict


    ########################################
    # Thermostat Action callbacks
    ########################################
    
    # Main thermostat action bottleneck called by Indigo Server.
 
   
    def actionControlThermostat(self, action, dev):
        self.logger.debug(u"{}: action.thermostatAction: {}".format(dev.name, action.thermostatAction))
       ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            self.handleChangeHvacModeAction(dev, action.actionMode)

        ###### SET FAN MODE ######
        elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
            self.handleChangeFanModeAction(dev, action.actionMode, u"hvacFanIsOn")

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
            self.update_needed = True

        ###### UNTRAPPED CONDITIONS ######
        # Explicitly show when nothing matches, indicates errors and unimplemented actions instead of quietly swallowing them
        else:
            self.logger.warning(u"{}: Unimplemented action.thermostatAction: {}".format(dev.name, action.thermostatAction))

    def actionControlUniversal(self, action, dev):
        self.logger.debug(u"{}: action.actionControlUniversal: {}".format(dev.name, action.deviceAction))
        if action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self.update_needed = True
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
        self.logger.debug(u"{} ({}): Mode set to: {}".format(dev.name, dev.address, hvac_mode))

        self.active_devices[dev.id].set_hvac_mode(hvac_mode)
        self.update_needed = True
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
        
        self.update_needed = True
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
            self.active_devices[dev.id].resume_program()

        self.update_needed = True
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
        EcobeeThermostat.temperatureFormatter = TEMP_FORMATTERS.get(value)


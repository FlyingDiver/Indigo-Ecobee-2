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
        self.logger.debug(u"macOS {}, Indigo {}".format(macOS, indigo.server.version))
        if int(macOS[3:5]) < 13:
            self.logger.error(u"Unsupported macOS version! {}".format(macOS))
                
        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "15")) *  60.0
        self.logger.debug(u"updateFrequency = " + str(self.updateFrequency))
        self.next_update = time.time() + self.updateFrequency
        
        self.ecobee_accounts = {}
        self.ecobee_thermostats = {}
        self.ecobee_remotes = {}

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
                    self.update_needed = False
                    self.next_update = time.time() + self.updateFrequency
                
                    # update from Ecobee servers
                    
                    for accountID, account in self.ecobee_accounts.items():
                        if account.authenticated:
                            account.server_update()
                        else:
                            self.logger.debug("Ecobee account {} not authenticated, skipping update".format(accountID))
                    
                    # now update all the Indigo devices         
                    
                    for dev in self.ecobee_thermostats.values():
                        dev.update()
                    
                    for dev in self.ecobee_remotes.values():
                        dev.update()
                    

                # Refresh the auth tokens as needed.  Refresh interval for each account is calculated during the refresh
                
                for accountID, account in self.ecobee_accounts.items():
                    if time.time() > account.next_refresh:
                        if account.authenticated:
                            account.do_token_refresh()                    
                            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF + str(accountID)] = account.refresh_token
                            self.savePluginPrefs()
                        else:
                            self.logger.error("Ecobee account {} not authenticated, skipping refresh".format(accountID))

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

        try:
            ecobee = self.ecobee_accounts[int(valuesDict["account"])]
        except:
            return []
            
        active_stats =  [
            (indigo.devices[dev].pluginProps["address"])
            for dev in self.ecobee_thermostats
        ]
        self.logger.threaddebug("get_thermostat_list: active_stats = {}".format(active_stats))

        available_stats =[]
        for iden, therm in ecobee.thermostats.items():
            if iden not in active_stats:
                available_stats.append((iden, therm["name"]))
        
        if targetId:
            try:
                dev = indigo.devices[targetId]
                available_stats.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass
                
        self.logger.threaddebug("get_thermostat_list: available_stats = {}".format(available_stats))
        return available_stats     

    def get_sensor_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug("get_sensor_list: typeId = {}, targetId = {}, valuesDict = {}".format(typeId, targetId, valuesDict))

        try:
            ecobee = self.ecobee_accounts[int(valuesDict["account"])]
        except:
            return []

        active_sensors =  [
            (indigo.devices[dev].pluginProps["address"])
            for dev in self.ecobee_remotes
        ]
        self.logger.threaddebug("get_thermostat_list: active_stats = {}".format(active_stats))
            
        available_sensors =[]
        for iden, therm in ecobee.thermostats.items():
            if iden not in active_stats:
                available_stats.append((iden, therm["name"]))
        
        if targetId:
            try:
                dev = indigo.devices[targetId]
                available_stats.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass
                
        self.logger.threaddebug("get_sensor_list: available_stats = {}".format(available_stats))
        return available_stats     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict


    ########################################

    def getDeviceConfigUiValues(self, pluginProps, typeId, devId):
        valuesDict = indigo.Dict(pluginProps)
        errorsDict = indigo.Dict()
        self.logger.debug("getDeviceConfigUiValues, typeID = {}, valuesDict = {}".format(typeId, valuesDict))
        return (valuesDict, errorsDict)

    def getDeviceFactoryUiValues(self, devIdList):
        self.logger.debug("getDeviceFactoryUiValues, devIdList = {}".format(devIdList))
        valuesDict = indigo.Dict()
        errorMsgDict = indigo.Dict()

        # change default to creating Thermostats if there's at least one account defined
        
        if len(self.ecobee_accounts) > 0:
            valuesDict["deviceType"] = "EcobeeThermostat"
            valuesDict["account"] = self.ecobee_accounts[self.ecobee_accounts.keys()[0]].dev.id
            
        return (valuesDict, errorMsgDict)

    def validateDeviceFactoryUi(self, valuesDict, devIdList):
        errorsDict = indigo.Dict()
        valid = True
        
        if valuesDict["deviceType"] == "EcobeeThermostat":
            if valuesDict["account"] == 0:
                errorsDict["account"] = "No Ecobee Account Specified"
                self.logger.warning("validateDeviceFactoryUi - No Ecobee Account Specified")
                valid = False
            
            if len(valuesDict["address"]) == 0:
                errorsDict["account"] = "No Thermostat Specified"
                self.logger.warning("validateDeviceFactoryUi - No Thermostat Specified")
                valid = False              

        elif valuesDict["deviceType"] == "EcobeeAccount":
            if valuesDict["authStatus"] != "Authenticated":
                errorsDict["authStatus"] = "Ecobee Account Not Authenticated"
                self.logger.warning("validateDeviceFactoryUi - Ecobee Account Not Authenticated")
                valid = False
        
        return (valid, valuesDict, errorsDict)

    def closedDeviceFactoryUi(self, valuesDict, userCancelled, devIdList):
        
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
            newProps["holdType"] = valuesDict["holdType"]
            newProps["createRemotes"] = valuesDict["createRemotes"]
            newdev.replacePluginPropsOnServer(newProps)                

        elif valuesDict["deviceType"] == "RemoteSensor":
        
            newdev = indigo.device.create(indigo.kProtocol.Plugin, deviceTypeId="RemoteSensor")
            newdev.model = "Ecobee Remote Sensor"
            newdev.replaceOnServer()

        self.logger.debug("closedDeviceFactoryUi complete, pluginProps =\n{}".format(newdev.pluginProps))
        return

    ######################
    #
    #  Subclass this if you dynamically need to change the device states list provided based on specific device instance data.
      
    def getDeviceStateList(self, dev):
        
        stateList = indigo.PluginBase.getDeviceStateList(self, dev)
        device_type = dev.pluginProps.get("device_type", None)
                                
        if device_type in ['athenaSmart', 'corSmart', 'apolloSmart']:

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
        
        elif device_type in ['nikeSmart']:

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
        

        elif device_type in ['idtSmart', 'siSmart']:

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
                                "Key"          : "fanMinOnTime", 
                                "StateLabel"   : "Minimum fan time",   
                                "TriggerLabel" : "Minimum fan time",   
                                "Type"         : 100 })
        
        return stateList

        
    def deviceStartComm(self, dev):

        self.logger.info(u"{}: Starting {} Device {}".format(dev.name, dev.deviceTypeId, dev.id))
        
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

            self.ecobee_thermostats[dev.id] = EcobeeThermostat(dev)
            self.update_needed = True

        elif dev.deviceTypeId == 'OccupancySensor':

            ### fixup code ###
            if not "-1" in dev.address:
                newProps = dev.pluginProps
                newProps["address"] = dev.address + "-1"
                dev.replacePluginPropsOnServer(newProps)                
                self.logger.debug(u"{}: Updated occupancy sensor address".format(dev.name))
            ###################
                
        elif dev.deviceTypeId == 'RemoteSensor':
            
            ### fixup code ###
            rawDev = indigo.rawServerRequest("GetDevice", {"ID": dev.id})
            if rawDev.get("GroupID", 0) != 0:
                rawDev["GroupID"] = 0
                indigo.rawServerCommand("ReplaceDevice", {"ID": dev.id, "Device": rawDev})
                self.logger.debug(u"{}: Removed remote sensor from device group".format(dev.name))
            ###################

            self.logger.debug(u"{}: starting RemoteSensor device:\n{}".format(dev.name, dev))
            

    def deviceStopComm(self, dev):

        self.logger.info(u"{}: Stopping {} Device {}".format( dev.name, dev.deviceTypeId, dev.id))

        if dev.deviceTypeId == 'EcobeeThermostat':
            if dev.id in self.ecobee_thermostats:
                del self.ecobee_thermostats[dev.id]
 
        elif dev.deviceTypeId == 'EcobeeAccount':
            if dev.id in self.ecobee_accounts:
                del self.ecobee_accounts[dev.id]
            
        elif dev.deviceTypeId == 'RemoteSensor':
            self.logger.threaddebug(u"{}: Disabling remote sensor, dev =\n{}".format(dev.name, dev))
            
                     
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
        defaultHold = dev.pluginProps.get("holdType", "nextTransition")

        climate = action.props.get("climate")
        holdType = action.props.get("holdType", defaultHold)
        self.ecobee_thermostats[dev.id].set_climate_hold(climate, holdType)

    def climateListGenerator(self, filter, valuesDict, typeId, targetId):                                                                                                                 
        self.logger.debug(u"climateListGenerator: typeId = {}, targetId = {}".format(typeId, targetId))
        return self.ecobee_thermostats[targetId].get_climates()

    ########################################
    # Set Hold Type
    ########################################
    
    def actionSetDefaultHoldType(self, action, dev):
        self.logger.debug(u"{}: actionSetDefaultHoldType".format(dev.name))
         
        props = dev.pluginProps
        props["holdType"] = action.props.get("holdType", "nextTransition")
        dev.replacePluginPropsOnServer(props)                
 
 
    ########################################
    # Resume Program callbacks
    ########################################
    
    def menuResumeAllPrograms(self):
        self.logger.debug(u"menuResumeAllPrograms")
        for devId, thermostat in self.ecobee_thermostats.items():
            if indigo.devices[devId].deviceTypeId == 'EcobeeThermostat':
                thermostat.resume_program()

    def menuResumeProgram(self, valuesDict, typeId):
        self.logger.debug(u"menuResumeProgram")
        try:
            deviceId = int(valuesDict["targetDevice"])
        except:
            self.logger.error(u"Bad Device specified for Resume Program operation")
            return False

        for thermId, thermostat in self.ecobee_thermostats.items():
            if thermId == deviceId:
                thermostat.resume_program()
        return True
        
    def menuDumpThermostat(self, valuesDict, typeId):
        self.logger.debug(u"menuDumpThermostat")
        try:
            deviceId = int(valuesDict["targetDevice"])
        except:
            self.logger.error(u"Bad Device specified for Write Thermostat Data operation")
            return False

        for thermId, thermostat in self.ecobee_thermostats.items():
            if thermId == deviceId:
                thermostat.dump_data()
        return True
        

    def actionResumeAllPrograms(self, action, dev):
        self.logger.debug(u"actionResumeAllPrograms")
        for devId, thermostat in self.ecobee_thermostats.items():
            if indigo.devices[devId].deviceTypeId == 'EcobeeThermostat':
                thermostat.resume_program()

    def actionResumeProgram(self, action, dev):
        self.logger.debug(u"{}: actionResumeProgram".format(dev.name))
        self.ecobee_thermostats[dev.id].resume_program()
    
    def pickThermostat(self, filter=None, valuesDict=None, typeId=0):
        retList = []
        for dev in indigo.devices.iter("self"):
            if dev.deviceTypeId == 'EcobeeThermostat':
                retList.append((dev.id, dev.name))
        retList.sort(key=lambda tup: tup[1])
        return retList



    ########################################
    # Process action request from Indigo Server to change main thermostat's main mode.
    ########################################

    def handleChangeHvacModeAction(self, dev, newHvacMode):
        hvac_mode = kHvacModeEnumToStrMap.get(newHvacMode, u"unknown")
        self.logger.debug(u"{} ({}): Mode set to: {}".format(dev.name, dev.address, hvac_mode))

        self.ecobee_thermostats[dev.id].set_hvac_mode(hvac_mode)
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

        holdType = dev.pluginProps.get("holdType", "nextTransition")

        if stateKey == u"setpointCool":
            self.logger.info(u'{}: set cool to: {} and leave heat at: {}'.format(dev.name, newSetpoint, dev.heatSetpoint))
            self.ecobee_thermostats[dev.id].set_hold_temp(newSetpoint, dev.heatSetpoint, holdType)

        elif stateKey == u"setpointHeat":
            self.logger.info(u'{}: set heat to: {} and leave cool at: {}'.format(dev.name, newSetpoint,dev.coolSetpoint))
            self.ecobee_thermostats[dev.id].set_hold_temp(dev.coolSetpoint, newSetpoint, holdType)

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
        holdType = dev.pluginProps.get("holdType", "nextTransition")
        
        if newFanMode == u"on":
            self.logger.info(u'{}: set fan to ON, leave cool at {} and heat at {}'.format(dev.name, dev.coolSetpoint,dev.heatSetpoint))
            self.ecobee_thermostats[dev.id].set_hold_temp_with_fan(dev.coolSetpoint, dev.heatSetpoint, holdType)

        if newFanMode == u"auto":
            self.logger.info(u'{}: resume normal program to set fan to Auto'.format(dev.name))
            self.ecobee_thermostats[dev.id].resume_program()

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


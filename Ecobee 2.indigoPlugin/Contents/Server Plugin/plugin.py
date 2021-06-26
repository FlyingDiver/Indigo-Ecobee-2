#! /usr/bin/env python
# -*- coding: utf-8 -*-

import json
import logging
import platform
import threading
import time

from ecobee_account import EcobeeAccount
from ecobee_devices import EcobeeDevice, EcobeeThermostat, RemoteSensor

import temperature_scale

REFRESH_TOKEN_PLUGIN_PREF='refreshToken-{}'
TEMPERATURE_SCALE_PLUGIN_PREF='temperatureScale'

ECOBEE_MODELS = {
    'Unknown'     :    'Unknown Device',
    'idtSmart'    :    'ecobee Smart',
    'siSmart'     :    'ecobee Si Smart',
    'athenaSmart' :    'ecobee3 Smart',
    'corSmart'    :    'Carrier or Bryant Cor',
    'nikeSmart'   :    'ecobee3 lite Smart',
    'apolloSmart' :    'ecobee4 Smart',
    'vulcanSmart' :    'ecobee Smart w/ Voice Control'
}

TEMP_CONVERTERS = {
    'F': temperature_scale.Fahrenheit(),
    'C': temperature_scale.Celsius(),
    'K': temperature_scale.Kelvin(),
    'R': temperature_scale.Rankine()
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

        self.logLevel = int(self.pluginPrefs.get(u"logLevel", logging.INFO))
        self.indigo_log_handler.setLevel(self.logLevel)
        self.logger.debug(u"logLevel = {}".format(self.logLevel))


    def startup(self):
        self.logger.info(u"Starting Ecobee")
       
       # this will break if Apple ever uses three part version numbers again
        macOS = platform.mac_ver()[0]
        self.logger.debug(u"macOS {}, Indigo {}".format(macOS, indigo.server.version))
        try:
            if float(macOS) < 10.13:
                self.logger.error(u"Unsupported macOS version! {}".format(macOS))
        except:
            self.logger.warning(u"Unable to verify macOS version! {}".format(macOS))
        
        self.updateFrequency = float(self.pluginPrefs.get('updateFrequency', "15")) *  60.0
        self.logger.debug(u"updateFrequency = {}".format(self.updateFrequency))
        self.next_update = time.time() + self.updateFrequency
        
        self.ecobee_accounts = {}
        self.ecobee_thermostats = {}
        self.ecobee_remotes = {}

        self.update_needed = False
        
        scale = self.pluginPrefs.get(TEMPERATURE_SCALE_PLUGIN_PREF, 'F')
        self.logger.debug(u'setting temperature scale to {}'.format(scale))
        EcobeeDevice.temperatureFormatter = TEMP_CONVERTERS[scale]


    def shutdown(self):
        self.logger.info(u"Stopping Ecobee")
        

    def validatePrefsConfigUi(self, valuesDict):
        self.logger.debug(u"validatePrefsConfigUi called")
        errorDict = indigo.Dict()

        updateFrequency = int(valuesDict['updateFrequency'])
        if (updateFrequency < 3) or (updateFrequency > 60):
            errorDict['updateFrequency'] = u"Update frequency is invalid - enter a valid number (between 3 and 60)"

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
            self.logger.debug(u"updateFrequency = {}".format(self.updateFrequency))
            self.next_update = time.time()

            scale = valuesDict[TEMPERATURE_SCALE_PLUGIN_PREF]
            self.logger.debug(u'setting temperature scale to {}'.format(scale))
            EcobeeDevice.temperatureFormatter = TEMP_CONVERTERS[scale]

            self.update_needed = True
        
    
    ########################################

    def deviceStartComm(self, dev):

        self.logger.info(u"{}: Starting {} Device {}".format(dev.name, dev.deviceTypeId, dev.id))
        
        dev.stateListOrDisplayStateIdChanged()

        if dev.deviceTypeId == 'EcobeeAccount':     # create the Ecobee account object.  It will attempt to refresh the auth token.
            
            ecobeeAccount = EcobeeAccount(dev, refresh_token = self.pluginPrefs.get(REFRESH_TOKEN_PLUGIN_PREF.format(dev.id), None))
            self.ecobee_accounts[dev.id] = ecobeeAccount
            
            dev.updateStateOnServer(key="authenticated", value=ecobeeAccount.authenticated)
            if ecobeeAccount.authenticated:
                self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF.format(dev.id)] = ecobeeAccount.refresh_token
                self.savePluginPrefs()

            self.update_needed = True
                            
        elif dev.deviceTypeId == 'EcobeeThermostat':

            self.ecobee_thermostats[dev.id] = EcobeeThermostat(dev)
            self.update_needed = True
            
        elif dev.deviceTypeId == 'RemoteSensor':
            
            self.ecobee_remotes[dev.id] = RemoteSensor(dev)
            self.update_needed = True

            ### fixup code - ungroup remote sensors ###
            rawDev = indigo.rawServerRequest("GetDevice", {"ID": dev.id})
            if rawDev.get("GroupID", 0):
                rawDev["GroupID"] = 0
                indigo.rawServerCommand("ReplaceDevice", {"ID": dev.id, "Device": rawDev})
                self.logger.debug(u"{}: Removed remote sensor from device group".format(dev.name))
            ###################
                
            
    def deviceStopComm(self, dev):

        self.logger.info(u"{}: Stopping {} Device {}".format( dev.name, dev.deviceTypeId, dev.id))

        if dev.deviceTypeId == 'EcobeeAccount':
            if dev.id in self.ecobee_accounts:
                del self.ecobee_accounts[dev.id]
            
        elif dev.deviceTypeId == 'EcobeeThermostat':
            if dev.id in self.ecobee_thermostats:
                del self.ecobee_thermostats[dev.id]
 
        elif dev.deviceTypeId == 'RemoteSensor':
            if dev.id in self.ecobee_remotes:
                del self.ecobee_remotes[dev.id]

    ########################################
        
    def runConcurrentThread(self):
        self.logger.debug(u"runConcurrentThread starting")
        try:
            while True:
                
                if (time.time() > self.next_update) or self.update_needed:
                    self.update_needed = False
                    self.next_update = time.time() + self.updateFrequency
                
                    # update from Ecobee servers
                    
                    for accountID, account in self.ecobee_accounts.items():
                        if account.authenticated:
                            account.server_update()
                            indigo.devices[accountID].updateStateImageOnServer(indigo.kStateImageSel.SensorOn)
                        else:
                            indigo.devices[accountID].updateStateImageOnServer(indigo.kStateImageSel.SensorTripped)
                            self.logger.debug("Ecobee account {} not authenticated, skipping update".format(accountID))

                    # now update all the Indigo devices         
                    
                    for stat in self.ecobee_thermostats.values():
                        stat.update()
                    
                    for remote in self.ecobee_remotes.values():
                        remote.update()

                # Refresh the auth tokens as needed.  Refresh interval for each account is calculated during the refresh
                
                for accountID, account in self.ecobee_accounts.items():
                    if time.time() > account.next_refresh:
                        account.do_token_refresh()                    
                        if account.authenticated:
                            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF.format(accountID)] = account.refresh_token
                            self.savePluginPrefs()

                self.sleep(1.0)

        except self.StopThread:
            self.logger.debug(u"runConcurrentThread ending")
            pass

                
    ########################################
    #
    # callbacks from device creation UI
    #
    ########################################

    def get_account_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug("get_account_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))
        accounts = [
            (account.devID, indigo.devices[account.devID].name)
            for account in self.ecobee_accounts.values()
        ]
        self.logger.debug("get_account_list: accounts = {}".format(accounts))
        return accounts
        

    def get_device_list(self, filter="", valuesDict=None, typeId="", targetId=0):
        self.logger.threaddebug("get_device_list: typeId = {}, targetId = {}, filter = {}, valuesDict = {}".format(typeId, targetId, filter, valuesDict))

        try:
            ecobee = self.ecobee_accounts[int(valuesDict["account"])]
        except:
            return []
        
        if valuesDict.get('deviceType', None):
            typeId = valuesDict['deviceType']
            
        if typeId == "EcobeeThermostat":
        
            active_stats =  [
                (indigo.devices[dev].pluginProps["address"])
                for dev in self.ecobee_thermostats
            ]
            self.logger.debug("get_device_list: active_stats = {}".format(active_stats))

            available_devices = []
            for iden, therm in ecobee.thermostats.items():
                if iden not in active_stats:
                    available_devices.append((iden, therm["name"]))
        
                
        elif typeId == "RemoteSensor":

            active_sensors =  [
                (indigo.devices[dev].pluginProps["address"])
                for dev in self.ecobee_remotes
            ]
            self.logger.debug("get_device_list: active_sensors = {}".format(active_sensors))
            
            available_devices = []
            for iden, sensor in ecobee.sensors.items():
                if iden not in active_sensors:
                    available_devices.append((iden, sensor["name"]))
                
        elif typeId == "EcobeeAccount":
            return []
            
        else:
            self.logger.warning("get_device_list: unknown typeId = {}".format(typeId))
            return []
          
        if targetId:
            try:
                dev = indigo.devices[targetId]
                available_devices.insert(0, (dev.pluginProps["address"], dev.name))
            except:
                pass

        self.logger.debug("get_device_list: available_devices for {} = {}".format(typeId, available_devices))
        return available_devices     

    # doesn't do anything, just needed to force other menus to dynamically refresh
    def menuChanged(self, valuesDict = None, typeId = None, devId = None):
        return valuesDict


    ########################################

    def getDeviceFactoryUiValues(self, devIdList):
        self.logger.debug("getDeviceFactoryUiValues: devIdList = {}".format(devIdList))

        valuesDict = indigo.Dict()
        errorMsgDict = indigo.Dict()

        # change default to creating Thermostats if there's at least one account defined
        
        if len(self.ecobee_accounts) > 0:
            valuesDict["deviceType"] = "EcobeeThermostat"
            valuesDict["account"] = self.ecobee_accounts[self.ecobee_accounts.keys()[0]].devID
            
        return (valuesDict, errorMsgDict)

    def validateDeviceFactoryUi(self, valuesDict, devIdList):
        self.logger.threaddebug("validateDeviceFactoryUi: valuesDict = {}, devIdList = {}".format(valuesDict, devIdList))
        errorsDict = indigo.Dict()
        valid = True
        
        if valuesDict["deviceType"] == "EcobeeThermostat":
            if valuesDict["account"] == 0:
                errorsDict["account"] = "No Ecobee Account Specified"
                self.logger.warning("validateDeviceFactoryUi - No Ecobee Account Specified")
                valid = False
            
            if len(valuesDict["address"]) == 0:
                errorsDict["address"] = "No Thermostat Specified"
                self.logger.warning("validateDeviceFactoryUi - No Thermostat Specified")
                valid = False              

        elif valuesDict["deviceType"] == "RemoteSensor":
            if valuesDict["account"] == 0:
                errorsDict["account"] = "No Ecobee Account Specified"
                self.logger.warning("validateDeviceFactoryUi - No Ecobee Account Specified")
                valid = False
            
            if len(valuesDict["address"]) == 0:
                errorsDict["address"] = "No Sensor Specified"
                self.logger.warning("validateDeviceFactoryUi - No Sensor Specified")
                valid = False              

        elif valuesDict["deviceType"] == "EcobeeAccount":
            if valuesDict["authStatus"] != "Authenticated":
                errorsDict["authStatus"] = "Ecobee Account Not Authenticated"
                self.logger.warning("validateDeviceFactoryUi - Ecobee Account Not Authenticated")
                valid = False
        
        return (valid, valuesDict, errorsDict)

    def closedDeviceFactoryUi(self, valuesDict, userCancelled, devIdList):
        
        if userCancelled:
            self.logger.debug("closedDeviceFactoryUi: user cancelled")
            return

        self.logger.threaddebug("closedDeviceFactoryUi: valuesDict =\n{}\ndevIdList =\n{}".format(valuesDict, devIdList))
            
        if valuesDict["deviceType"] == "EcobeeAccount":
        
            dev = indigo.device.create(indigo.kProtocol.Plugin, deviceTypeId="EcobeeAccount")
            dev.model = "Ecobee Account"
            dev.name = "Ecobee Account ({})".format(dev.id)
            dev.replaceOnServer()

            self.logger.info(u"Created EcobeeAccount device '{}'".format(dev.name))

        elif valuesDict["deviceType"] == "EcobeeThermostat":
        
            address = valuesDict["address"]

            ecobee = self.ecobee_accounts[valuesDict["account"]]
            thermostat = ecobee.thermostats.get(address)
            name = "Ecobee {}".format(thermostat.get('name'))            
            device_type = thermostat.get('modelNumber', 'Unknown')

            dev = indigo.device.create(indigo.kProtocol.Plugin, address = address, name = name, deviceTypeId="EcobeeThermostat")
            dev.model = "Ecobee Thermostat"
            dev.subModel = ECOBEE_MODELS.get(device_type, 'Unknown')
            dev.replaceOnServer()

            self.logger.info(u"Created EcobeeThermostat device '{}'".format(dev.name))

            newProps = dev.pluginProps
            newProps["account"] = valuesDict["account"]
            newProps["holdType"] = valuesDict["holdType"]
            newProps["device_type"] = device_type
            newProps["NumHumidityInputs"] = 1
            newProps["ShowCoolHeatEquipmentStateUI"] = True

            # set props based on device type
        
            if device_type in ['athenaSmart', 'apolloSmart', 'nikeSmart', 'vulcanSmart']: 
                newProps["NumTemperatureInputs"] = 2

            if device_type in ['athenaSmart', 'apolloSmart', 'corSmart', 'vulcanSmart']:    # Has integral occupancy sensor.
            
                sensor_name = dev.name + " Occupancy"
                self.logger.info(u"Adding Occupancy Sensor '{}' to '{}'".format(sensor_name, dev.name))
                newdev = indigo.device.create(indigo.kProtocol.Plugin, address = dev.address, name = sensor_name, folder = dev.folderId,
                            deviceTypeId = "OccupancySensor", props = { 'SupportsStatusRequest': False, 'account': valuesDict["account"] })   
                newdev.model = dev.model
                newdev.subModel = "Occupancy"
                newdev.replaceOnServer()    
                newProps["occupancy"] = newdev.id
                self.logger.info(u"Created EcobeeThermostat Occupancy device '{}'".format(newdev.name))
            
            if device_type in ['athenaSmart', 'apolloSmart', 'nikeSmart', 'vulcanSmart']:        # Supports linked remote sensors
            
                remotes = thermostat.get("remotes")
                self.logger.debug(u"{}: {} remotes".format(dev.name, len(remotes)))
                
                # Hack to create remote sensors after closedDeviceFactoryUi has completed.  If created here, they would automatically
                # become part of the device group, which we don't want.
                if valuesDict["createRemotes"] and len(remotes) > 0:
                    delayedCreate = threading.Timer(0.5, lambda: self.createRemoteSensors(dev, remotes))
                    delayedCreate.start()
               
                else:
                    self.logger.debug(u"{}: Not creating remotes".format(dev.name))
                           
            dev.replacePluginPropsOnServer(newProps)

        elif valuesDict["deviceType"] == "RemoteSensor":

            address = valuesDict["address"]
            ecobee = self.ecobee_accounts[valuesDict["account"]]
            remote_name = ecobee.sensors.get(address).get('name')
            thermostat_id = ecobee.sensors.get(address).get("thermostat")
            thermostat_name = ecobee.thermostats.get(thermostat_id).get('name')     
            name = "Ecobee {} Remote - {}".format(thermostat_name, remote_name)
        
            newdev = indigo.device.create(indigo.kProtocol.Plugin, address = valuesDict["address"], name = name, deviceTypeId="RemoteSensor", 
                        props = { 'SupportsStatusRequest': False, 'SupportsSensorValue': True, 'SupportsSensorValue': True, 'account': valuesDict["account"]})
            newdev.model = "Ecobee Remote Sensor"
            newdev.replaceOnServer()

            self.logger.info(u"Created RemoteSensor device '{}'".format(newdev.name))

        return

    def createRemoteSensors(self, dev, remotes):

        self.logger.debug("{}: createRemoteSensors starting".format(dev.name))
        remote_ids = indigo.Dict()

        for code, rem in remotes.items():

            for rdev in indigo.devices.iter("self"):
                if rdev.deviceTypeId == 'RemoteSensor' and rdev.address == code:    # remote device already exists
                    self.logger.debug(u"Remote sensor device {} already exists".format(rdev.address))
            else:

                remote_name = "{} Remote - {}".format(dev.name, rem["name"])
                self.logger.info(u"Adding Remote Sensor '{}' to '{}'".format(remote_name, dev.name))
                newdev = indigo.device.create(indigo.kProtocol.Plugin, address = code, name = remote_name, folder=dev.folderId,
                            deviceTypeId="RemoteSensor", props={ 'SupportsSensorValue': True, 'SupportsStatusRequest': False, 'account': dev.pluginProps["account"] })
                newdev.model = dev.model
                newdev.subModel = "Remote"
                newdev.replaceOnServer()
                newdev.updateStateImageOnServer(indigo.kStateImageSel.TemperatureSensor)
                remote_ids[code] = str(newdev.id)

        newProps = dev.pluginProps
        newProps["remotes"] = remote_ids
        dev.replacePluginPropsOnServer(newProps)
          

    ######################
    #
    #  Subclass this if you dynamically need to change the device states list provided based on specific device instance data.
      
    def getDeviceStateList(self, dev):
        
        stateList = indigo.PluginBase.getDeviceStateList(self, dev)
        device_type = dev.pluginProps.get("device_type", None)
                                
        if device_type in ['athenaSmart', 'corSmart', 'apolloSmart', 'vulcanSmart']:

            stateList.append({  "Disabled"     : False, 
                                "Key"          : "latestEventType", 
                                "StateLabel"   : "Last Event",   
                                "TriggerLabel" : "Last Event",   
                                "Type"         : 150 })
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
                                "Key"          : "latestEventType", 
                                "StateLabel"   : "Last Event",   
                                "TriggerLabel" : "Last Event",   
                                "Type"         : 150 })
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
                                "Key"          : "latestEventType", 
                                "StateLabel"   : "Last Event",   
                                "TriggerLabel" : "Last Event",   
                                "Type"         : 150 })
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
            
                     
#    Authentication Step 1, called from Devices.xml

    def request_pin(self, valuesDict, typeId, devId):
        if devId in self.ecobee_accounts:
            self.temp_ecobeeAccount = self.ecobee_accounts[devId]
            self.logger.debug(u"request_pin: using existing Ecobee account {}".format(self.temp_ecobeeAccount.devID))
        else:
            self.temp_ecobeeAccount = EcobeeAccount(None, None)
            self.logger.debug(u"request_pin: using temporary Ecobee account object")
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
            self.pluginPrefs[REFRESH_TOKEN_PLUGIN_PREF.format(devId)] = self.temp_ecobeeAccount.refresh_token
            self.savePluginPrefs()
        else:
            valuesDict["authStatus"] = "Token Request Failed"
        return valuesDict


    ########################################
    # Thermostat Action callbacks
    ########################################
    
    # Main thermostat action bottleneck called by Indigo Server.
   
    def actionControlThermostat(self, action, device):
        self.logger.debug(u"{}: action.thermostatAction: {}, action.actionValue: {}, setpointHeat: {}, setpointCool: {}".format(device.name, 
            action.thermostatAction, action.actionValue, device.heatSetpoint, device.coolSetpoint))
            
       ###### SET HVAC MODE ######
        if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
            self.handleChangeHvacModeAction(device, action.actionMode)

        ###### SET FAN MODE ######
        elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
            self.handleChangeFanModeAction(device, action.actionMode, u"hvacFanIsOn")

        ###### SET COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetCoolSetpoint:
            newSetpoint = action.actionValue
            self.handleChangeSetpointAction(device, newSetpoint, u"setpointCool")

        ###### SET HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
            newSetpoint = action.actionValue
            self.handleChangeSetpointAction(device, newSetpoint, u"setpointHeat")

        ###### DECREASE/INCREASE COOL SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
            newSetpoint = device.coolSetpoint - action.actionValue
            self.handleChangeSetpointAction(device, newSetpoint, u"setpointCool")

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
            newSetpoint = device.coolSetpoint + action.actionValue
            self.handleChangeSetpointAction(device, newSetpoint, u"setpointCool")

        ###### DECREASE/INCREASE HEAT SETPOINT ######
        elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
            newSetpoint = device.heatSetpoint - action.actionValue
            self.handleChangeSetpointAction(device, newSetpoint, u"setpointHeat")

        elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
            newSetpoint = device.heatSetpoint + action.actionValue
            self.handleChangeSetpointAction(device, newSetpoint, u"setpointHeat")

        ###### REQUEST STATE UPDATES ######
        elif action.thermostatAction in [indigo.kThermostatAction.RequestStatusAll, indigo.kThermostatAction.RequestMode,
         indigo.kThermostatAction.RequestEquipmentState, indigo.kThermostatAction.RequestTemperatures, indigo.kThermostatAction.RequestHumidities,
         indigo.kThermostatAction.RequestDeadbands, indigo.kThermostatAction.RequestSetpoints]:
            self.update_needed = True

        ###### UNTRAPPED CONDITIONS ######
        # Explicitly show when nothing matches, indicates errors and unimplemented actions instead of quietly swallowing them
        else:
            self.logger.warning(u"{}: Unimplemented action.thermostatAction: {}".format(device.name, action.thermostatAction))

    def actionControlUniversal(self, action, device):
        self.logger.debug(u"{}: action.actionControlUniversal: {}".format(device.name, action.deviceAction))
        if action.deviceAction == indigo.kUniversalAction.RequestStatus:
            self.update_needed = True
        else:
            self.logger.warning(u"{}: Unimplemented action.deviceAction: {}".format(device.name, action.deviceAction))


    ########################################
    # Activate Comfort Setting callback
    ########################################
    
    def actionActivateComfortSetting(self, action, device):
        self.logger.debug(u"{}: actionActivateComfortSetting".format(device.name))
        defaultHold = device.pluginProps.get("holdType", "nextTransition")

        climate = action.props.get("climate")
        holdType = action.props.get("holdType", defaultHold)
        self.ecobee_thermostats[device.id].set_climate_hold(climate, holdType)

    def climateListGenerator(self, filter, valuesDict, typeId, targetId):                                                                                                                 
        self.logger.debug(u"climateListGenerator: typeId = {}, targetId = {}".format(typeId, targetId))
        return self.ecobee_thermostats[targetId].get_climates()

    ########################################
    # Set Hold Type
    ########################################
    
    def actionSetDefaultHoldType(self, action, device):
        self.logger.debug(u"{}: actionSetDefaultHoldType".format(device.name))
         
        props = device.pluginProps
        props["holdType"] = action.props.get("holdType", "nextTransition")
        device.replacePluginPropsOnServer(props)                
 
 
    ########################################
    # Process action request from Indigo Server to change main thermostat's main mode.
    ########################################

    def handleChangeHvacModeAction(self, device, newHvacMode):
        hvac_mode = kHvacModeEnumToStrMap.get(newHvacMode, u"unknown")
        self.logger.debug(u"{} ({}): Mode set to: {}".format(device.name, device.address, hvac_mode))

        self.ecobee_thermostats[device.id].set_hvac_mode(hvac_mode)
        self.update_needed = True
        if "hvacOperationMode" in device.states:
            device.updateStateOnServer("hvacOperationMode", newHvacMode)

    ########################################
    # Process action request from Indigo Server to change a cool/heat setpoint.
    ########################################
    
    def handleChangeSetpointAction(self, device, newSetpoint, stateKey):
        self.logger.debug(u"{}: handleChangeSetpointAction, newSetpoint: {}, stateKey: {}".format(device.name, newSetpoint, stateKey))

        #   enforce minima/maxima based on the scale in use by the plugin
        #newSetpoint = EcobeeDevice.temperatureFormatter.bounds(newSetpoint)
        #self.logger.debug(u"{}: Bounded setpoint: {}".format(device.name, newSetpoint))

        ebSetpoint = EcobeeDevice.temperatureFormatter.convertToEcobee(newSetpoint)
        self.logger.debug(u"{}: Converted setpoint: {}".format(device.name, ebSetpoint))

        holdType = device.pluginProps.get("holdType", "nextTransition")

        if stateKey == u"setpointCool":
            self.logger.info(u'{}: set cool setpoint to: {}'.format(device.name, newSetpoint))
            self.ecobee_thermostats[device.id].set_hold_cool(ebSetpoint, holdType)

        elif stateKey == u"setpointHeat":
            self.logger.info(u'{}: set heat setpoint to: {}'.format(device.name, newSetpoint))
            self.ecobee_thermostats[device.id].set_hold_heat(ebSetpoint, holdType)

        else:
            self.logger.error(u'{}: handleChangeSetpointAction Invalid operation - {}'.format(device.name, stateKey))
            return
            
        self.update_needed = True
        if stateKey in device.states:
            self.logger.debug(u'{}: updating state {} to: {}'.format(device.name, stateKey, newSetpoint))
            device.updateStateOnServer(stateKey, newSetpoint, uiValue="{0:.1f} °F".format(newSetpoint))


    ########################################
    # Process action request from Indigo Server to change fan mode.
    ########################################
    
    def handleChangeFanModeAction(self, device, requestedFanMode, stateKey):
       
        newFanMode = kFanModeEnumToStrMap.get(requestedFanMode, u"auto")
        holdType = device.pluginProps.get("holdType", "nextTransition")
        
        if newFanMode == u"on":
            self.logger.info(u'{}: set fan to ON, leave cool at {} and heat at {}'.format(device.name, device.coolSetpoint, device.heatSetpoint))
            self.ecobee_thermostats[device.id].set_hold_temp_with_fan(EcobeeDevice.temperatureFormatter.convertToEcobee(device.coolSetpoint), 
                EcobeeDevice.temperatureFormatter.convertToEcobee(device.heatSetpoint), holdType)

        if newFanMode == u"auto":
            self.logger.info(u'{}: resume normal program to set fan to Auto'.format(device.name))
            self.ecobee_thermostats[device.id].resume_program()

        self.update_needed = True
        if stateKey in device.states:
            device.updateStateOnServer(stateKey, requestedFanMode, uiValue="True")


 
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
        
    def menuDumpThermostat(self):
        self.logger.debug(u"menuDumpThermostat")
        for accountID, account in self.ecobee_accounts.items():
            account.dump_data()
        return True

    def actionResumeAllPrograms(self, action):
        self.logger.debug(u"actionResumeAllPrograms")
        for devId, thermostat in self.ecobee_thermostats.items():
            if indigo.devices[devId].deviceTypeId == 'EcobeeThermostat':
                thermostat.resume_program()

    def actionResumeProgram(self, action, device):
        self.logger.debug(u"{}: actionResumeProgram".format(device.name))
        self.ecobee_thermostats[device.id].resume_program()
    
    def pickThermostat(self, filter=None, valuesDict=None, typeId=0):
        retList = []
        for device in indigo.devices.iter("self"):
            if device.deviceTypeId == 'EcobeeThermostat':
                retList.append((device.id, device.name))
        retList.sort(key=lambda tup: tup[1])
        return retList



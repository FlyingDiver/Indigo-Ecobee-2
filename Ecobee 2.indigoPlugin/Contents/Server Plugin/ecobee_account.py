#! /usr/bin/env python
# -*- coding: utf-8 -*-

import requests
import json
import time
import logging

import indigo

#
# All interactions with the Ecobee servers are encapsulated in this class
#

API_KEY = "opSMO6RtoUlhoAtlQehNZdaOZ6EQBO6Q"

class EcobeeAccount:

    def __init__(self, dev, refresh_token = None):
        self.logger = logging.getLogger("Plugin.EcobeeAccount")
        self.authenticated = False
        self.next_refresh = time.time()
        self.thermostats = {}
        self.sensors = {}
        self.access_token = None
        self.refresh_token = None
    
        if not dev:             # temp account objects created during PIN authentication don't have an associated device 
            return
                    
        self.devID = dev.id

        if refresh_token:
            self.logger.info(u"{}: EcobeeAccount created using refresh token = {}".format(dev.name, refresh_token))
            self.refresh_token = refresh_token
            self.do_token_refresh()
           
#
#   Ecobee Authentication functions
#

    # Authentication Step 1
    def request_pin(self):
        
        params = {'response_type': 'ecobeePin', 'client_id': API_KEY, 'scope': 'smartWrite'}
        try:
            request = requests.get('https://api.ecobee.com/authorize', params=params)
        except requests.RequestException, e:
            self.logger.error("PIN Request Error, exception = {}".format(e))
            return None
            
        if request.status_code == requests.codes.ok:
            self.authorization_code = request.json()['code']
            pin = request.json()['ecobeePin']
            self.logger.info("PIN Request OK, pin = {}".format(pin))
            return pin
            
        else:
            self.logger.error("PIN Request failed, response = '{}'".format(request.text))                
            return None

    # Authentication Step 2 is done on the Ecobee website using the PIN from step 1.

    # Authentication Step 3
    def get_tokens(self):
    
        params = {'grant_type': 'ecobeePin', 'code': self.authorization_code, 'client_id': API_KEY, 'ecobee_type': 'jwt'}
        try:
            request = requests.post('https://api.ecobee.com/token', params=params)
        except requests.RequestException, e:
            self.logger.error("Token Request Error, exception = {}".format(e))
            self.authenticated = False
            return
            
        if request.status_code == requests.codes.ok:
            self.logger.info("Token Request OK")
            self.access_token = request.json()['access_token']
            self.refresh_token = request.json()['refresh_token']
            self.next_refresh = time.time() + (float(request.json()['expires_in']) * 0.80)
            self.authenticated = True
        else:
            self.logger.error("Token Request failed, response = '{}'".format(request.text))                
            self.authenticated = False


    # called from __init__ or main loop to refresh the access tokens

    def do_token_refresh(self):
        if not self.refresh_token:
            self.authenticated = False
            return
   
        dev = indigo.devices[self.devID]     
        self.logger.debug(u"{}: Token Refresh, old refresh_token = {}".format(dev.name, self.refresh_token))

        params = {'grant_type': 'refresh_token', 'refresh_token': self.refresh_token, 'client_id': API_KEY, 'ecobee_type': 'jwt'}
        try:
            request = requests.post('https://api.ecobee.com/token', params=params)
        except requests.RequestException, e:
            self.logger.warning("Token Refresh Error, exception = {}".format(e))
            self.next_refresh = time.time() + 300.0         # try again in five minutes
            return
            
        if request.status_code == requests.codes.ok:
            if self.access_token and request.json()['access_token'] == self.access_token:
                self.logger.debug(u"{}: Access Token did not change".format(dev.name))
            else:
                self.access_token = request.json()['access_token']
                self.logger.debug(u"{}: Token Refresh OK, new access_token = {}".format(dev.name, self.access_token))
            
            if self.refresh_token and request.json()['refresh_token'] == self.refresh_token:
                self.logger.debug(u"{}: Refresh Token did not change".format(dev.name))
            else:
                self.refresh_token = request.json()['refresh_token']
                self.logger.info(u"{}: Token Refresh OK, new refresh_token: {}".format(dev.name, self.refresh_token))

            self.next_refresh = time.time() + (float(request.json()['expires_in']) * 0.80)
            self.authenticated = True
            return
            
        try:
            error = request.json()['error']
            if error == 'invalid_grant':
                self.logger.error(u"{}: Token refresh failed, will retry in 5 minutes.".format(dev.name))
                self.authenticated = False   
            else:                           
                self.logger.error(u"{}: Token Refresh Error, error = {}".format(dev.name, error))
        except:
            pass

        self.next_refresh = time.time() + 300.0         # try again in five minutes

        
#
#   Ecobee API functions
#
        
#   Request all thermostat data from the Ecobee servers.

    def server_update(self):
    
        dev = indigo.devices[self.devID]     
    
        header = {'Content-Type': 'application/json;charset=UTF-8',
                  'Authorization': 'Bearer ' + self.access_token}
        params = {'json': ('{"selection":{"selectionType":"registered",'
                            '"includeRuntime":"true",'
                            '"includeSensors":"true",'
                            '"includeEvents":"true",'
                            '"includeProgram":"true",'
                            '"includeEquipmentStatus":"true",'
                            '"includeSettings":"true"}}')}
        try:
            request = requests.get('https://api.ecobee.com/1/thermostat', headers=header, params=params)
        except requests.RequestException, e:
            self.logger.error(u"{}: Ecobee Account Update Error, exception = {}".format(dev.name, e))
            return
            
        if request.status_code != requests.codes.ok:
            self.logger.error(u"{}: Ecobee Account Update failed, response = '{}'".format(dev.name, request.text))                
            return
            
        stat_data = request.json()['thermostatList']
        status = request.json()['status']
        if status["code"] == 0:
            self.logger.debug(u"{}: Ecobee Account Update OK, got info on {} thermostats".format(dev.name, len(stat_data)))
        else:
            self.logger.warning(u"{}: Ecobee Account Update Error, code  = {}, message = {}.".format(dev.name, status["code"], status["message"]))
            return

        self.logger.threaddebug(json.dumps(stat_data, sort_keys=True, indent=4, separators=(',', ': ')))
            
        # Extract the relevant info from the server data and put it in a convenient Dict form
        
        for therm in stat_data:
            self.logger.debug(u"{}: getting data for '{}', {}".format(dev.name, therm[u"name"], therm[u"identifier"]))
            
            identifier = therm["identifier"]
            self.thermostats[identifier] = {    
                "name"              : therm["name"], 
                "brand"             : therm["brand"], 
                "features"          : therm["features"], 
                "modelNumber"       : therm["modelNumber"],
                "equipmentStatus"   : therm["equipmentStatus"],
                "currentClimate"    : therm["program"]["currentClimateRef"],
                "hvacMode"          : therm["settings"]["hvacMode"],
                "fanMinOnTime"      : therm["settings"]["fanMinOnTime"],
                "desiredCool"       : therm["runtime"]["desiredCool"],
                "desiredHeat"       : therm["runtime"]["desiredHeat"],
                "actualTemperature" : therm["runtime"]["actualTemperature"],
                "actualHumidity"    : therm["runtime"]["actualHumidity"],
                "desiredFanMode"    : therm["runtime"]["desiredFanMode"]
            }

            if therm.get('events') and len(therm.get('events')) > 0:
                self.thermostats[identifier]["latestEventType"] = therm.get('events')[0].get('type')
            else:
                self.thermostats[identifier]["latestEventType"] = None

            climates = {}
            for c in therm["program"]["climates"]:
                climates[c["climateRef"]] = c["name"]
            self.thermostats[identifier]["climates"] = climates
                
            remotes = {}
            for remote in therm[u"remoteSensors"]:

                if remote["type"] == "ecobee3_remote_sensor":
                    self.logger.debug(u"{}: getting data for remote sensor '{}', {}".format(dev.name, remote[u"name"], remote[u"code"]))
                    code = remote[u"code"]
                    remote_data = {u"name" : remote[u"name"], u"thermostat" : identifier}
                    for cap in remote["capability"]:
                        remote_data[cap["type"]] = cap["value"]
                    self.sensors[code] = remote_data
                    remotes[code] = remote_data

                elif remote["type"] == "thermostat":
                    internal = {}
                    for cap in remote["capability"]:
                        internal[cap["type"]] = cap["value"]
                    self.thermostats[identifier]["internal"] = internal

                elif remote["type"] == "monitor_sensor":
                    internal = {}
                    for cap in remote["capability"]:
                        if cap["type"] == "occupancy":
                            internal[cap["type"]] = cap["value"]
                    self.thermostats[identifier]["internal"] = internal

            self.thermostats[identifier]["remotes"] = remotes
            
        dev.updateStateOnServer(key="last_update", value=time.strftime("%d %b %Y %H:%M:%S"))

        self.logger.threaddebug("Thermostat Update, thermostats =\n{}\nsensors = {}\n".format(json.dumps(self.thermostats, sort_keys=True, indent=4, separators=(',', ': ')),
                                                                                        json.dumps(self.sensors, sort_keys=True, indent=4, separators=(',', ': '))))
    def dump_data(self):

        self.logger.info(json.dumps(self.thermostats, sort_keys=True, indent=4, separators=(',', ': ')))
        self.logger.info(json.dumps(self.sensors, sort_keys=True, indent=4, separators=(',', ': ')))
             
            
#   Generic routine for other API calls

    def make_request(self, body, log_msg_action):
        url = 'https://api.ecobee.com/1/thermostat'
        header = {'Content-Type': 'application/json;charset=UTF-8',
                 'Authorization': 'Bearer ' + self.access_token}
        params = {'format': 'json'}
        try:
            request = requests.post(url, headers=header, params=params, json=body)
        except RequestException:
            self.logger.error("API Error connecting to Ecobee.  Possible connectivity outage. Could not make request: {}".format(log_msg_action))
            return None
            
        if not request.status_code == requests.codes.ok:
            self.logger.warning("API '{}' request failed, result = {}".format(log_msg_action, request.text))
            return None

        serverStatus = request.json()['status']
        if serverStatus["code"] == 0:
            self.logger.debug("API '{}' request completed, result = {}".format(log_msg_action, request))
        else:
            self.logger.warning("API '{}' request error, code  = {}, message = {}.".format(log_msg_action, serverStatus["code"], serverStatus["message"]))




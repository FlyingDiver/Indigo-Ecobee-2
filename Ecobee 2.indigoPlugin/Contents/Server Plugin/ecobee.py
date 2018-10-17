import requests
import json
import logging

#
# All interactions with the Ecobee servers are encapsulated in this class
#

class EcobeeAccount:

    def __init__(self, api_key, refresh_token = None):
        self.logger = logging.getLogger("Plugin.EcobeeAccount")
        self.serverData = None
        self.authenticated = False
        
        self.api_key = api_key
        
        if refresh_token:
            self.refresh_token = refresh_token
            self.refresh_tokens()
            
        if self.authenticated:
            self.server_update()
        
#
#   Data fetching functions
#

    def get_thermostats(self):
        return self.serverData

    def get_thermostat(self, address):
        ths = self.get_thermostats()
        if not ths:
            return None
        
        return [
            th for th in ths
            if address == th.get('identifier')
        ][0]

    def get_remote_sensors(self):
        return [
            rs
            for th in self.get_thermostats()
            for rs in th['remoteSensors']
                if ('ecobee3_remote_sensor' == rs.get('type'))
        ]

    def get_remote_sensor(self, address):
        rss = self.get_remote_sensors()
        self.logger.threaddebug("looking for remote sensor %s in %s" % (address, rss))
        return [
            rs for rs in rss
            if address == rs.get('code')
        ][0]

    def get_climates(self, address):
        return [
            (rs.get('climateRef'), rs.get('name'))
            for rs in self.get_climates(address)
        ]

    def get_climates(self, address):
        thermostat = self.get_thermostat(address)
        return thermostat.get('program').get('climates')


#
#   Ecobee Authentication functions
#

    # Authentication Step 1
    def request_pin(self):
        
        params = {'response_type': 'ecobeePin', 'client_id': self.api_key, 'scope': 'smartWrite'}
        try:
            request = requests.get('https://api.ecobee.com/authorize', params=params)
        except RequestException:
            self.logger.error("PIN Request Error connecting to Ecobee.  Possible connectivity outage.")
            return None
            
        if request.status_code == requests.codes.ok:
            self.authorization_code = request.json()['code']
            pin = request.json()['ecobeePin']
            self.logger.debug("PIN Request OK, pin = {}. authorization_code = {}".format(pin, self.authorization_code))
            return pin
            
        else:
            error = request.json()['error']
            error_description = request.json()['error_description']
            self.logger.error("PIN Request failed, code {}, error '{}', description '{}'".format(request.status_code, error, error_description))
            return None

    # Authentication Step 3
    def get_tokens(self):
    
        params = {'grant_type': 'ecobeePin', 'code': self.authorization_code, 'client_id': self.api_key}
        try:
            request = requests.post('https://api.ecobee.com/token', params=params)
        except RequestException:
            self.logger.error("Token Request Error connecting to Ecobee.  Possible connectivity outage.")
            self.authenticated = False
            return
            
        if request.status_code == requests.codes.ok:
            self.access_token = request.json()['access_token']
            self.refresh_token = request.json()['refresh_token']
            self.logger.debug("Token Request OK, access_token = {}. refresh_token = {}".format(self.access_token, self.refresh_token))
            self.authenticated = True
        else:
            error = request.json()['error']
            error_description = request.json()['error_description']
            self.logger.error("Token Request failed, code {}, error '{}', description '{}'".format(request.status_code, error, error_description))
            self.authenticated = False


    # called from __init__ or main loop to refresh the access tokens

    def refresh_tokens(self):
        if not self.refresh_token:
            self.authenticated = False
            return
            
        self.logger.debug("Token Request with refresh_token = {}".format(self.refresh_token))

        params = {'grant_type': 'refresh_token', 'refresh_token': self.refresh_token, 'client_id': self.api_key}
        try:
            request = requests.post('https://api.ecobee.com/token', params=params)
        except RequestException:
            self.logger.error("Token Refresh Error connecting to Ecobee.  Possible connectivity outage.")
            self.authenticated = False
            return
            
        if request.status_code == requests.codes.ok:
            self.access_token = request.json()['access_token']
            self.refresh_token = request.json()['refresh_token']
            self.logger.debug("Token Refresh OK, access_token = {}. refresh_token = {}".format(self.access_token, self.refresh_token))
            self.authenticated = True
           
        else:
            error = request.json()['error']
            error_description = request.json()['error_description']
            self.logger.error("Token Refresh failed, code {}, error '{}', description '{}'".format(request.status_code, error, error_description))
            self.authenticated = False

        
#
#   Ecobee API functions
#
        
#   Request all thermostat data from the Ecobee servers.

    def server_update(self):
    
        header = {'Content-Type': 'application/json;charset=UTF-8',
                  'Authorization': 'Bearer ' + self.access_token}
        params = {'json': ('{"selection":{"selectionType":"registered",'
                            '"includeRuntime":"true",'
                            '"includeSensors":"true",'
                            '"includeProgram":"true",'
                            '"includeEquipmentStatus":"true",'
                            '"includeEvents":"true",'
                            '"includeWeather":"true",'
                            '"includeSettings":"true"}}')}
        try:
            request = requests.get('https://api.ecobee.com/1/thermostat', headers=header, params=params)
        except RequestException:
            self.logger.error("Thermostat Update Error connecting to Ecobee.  Possible connectivity outage.")
            return
            
        if request.status_code == requests.codes.ok:
            self.serverData = request.json()['thermostatList']
            self.logger.threaddebug("Thermostat Update OK, got info on {} devices".format(len(self.serverData)))
        else:
            error = request.json()['error']
            error_description = request.json()['error_description']
            self.logger.error("Thermostat Update failed, code {}, error '{}', description '{}'".format(request.status_code, error, error_description))

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
            
        if request.status_code == requests.codes.ok:
            self.logger.threaddebug("API '{}' request completed, result = {}".format(log_msg_action, request))
            return request
        else:
            error = request.json()['error']
            error_description = request.json()['error_description']
            self.logger.error("API Error while attempting to {}. Error code {}, error '{}', description '{}'.".format(log_msg_action, request.status_code, error, error_description))
            return None

    def set_hvac_mode(self, id, hvac_mode):
        ''' possible hvac modes are auto, auxHeatOnly, cool, heat, off '''
        body = {"selection": {"selectionType": "thermostats",
                              "selectionMatch": id },
                              "thermostat": {
                                  "settings": {
                                      "hvacMode": hvac_mode
                                  }
                              }}
        log_msg_action = "set HVAC mode by ID"
        return self.make_request(body, log_msg_action)


    def set_fan_min_on_time(self, id, fan_min_on_time):
        ''' The minimum time, in minutes, to run the fan each hour. Value from 1 to 60 '''
        body = {"selection": {"selectionType": "thermostats",
                        "selectionMatch": id },
                        "thermostat": {
                            "settings": {
                                "fanMinOnTime": fan_min_on_time
                            }
                        }}
        log_msg_action = "set fan minimum on time."
        return self.make_request(body, log_msg_action)

    def set_fan_mode(self, id, fan_mode, cool_temp, heat_temp, hold_type="nextTransition"):
        ''' Set fan mode. Values: auto, minontime, on '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "setHold", "params": {
                    "holdType": hold_type,
                    "coolHoldTemp": int(cool_temp * 10),
                    "heatHoldTemp": int(heat_temp * 10),
                    "fan": fan_mode
                }}]}
        log_msg_action = "set fan mode"
        return self.make_request(body, log_msg_action)

    def set_hold_temp(self, id, cool_temp, heat_temp, hold_type="nextTransition"):
        ''' Set a hold '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "setHold", "params": {
                    "holdType": hold_type,
                    "coolHoldTemp": int(cool_temp * 10),
                    "heatHoldTemp": int(heat_temp * 10)
                }}]}
        log_msg_action = "set hold temp by ID"
        return self.make_request(body, log_msg_action)

    def set_hold_temp_with_fan(self, id, cool_temp, heat_temp, hold_type="nextTransition"):
        ''' Set a fan hold '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "setHold", "params": {
                    "holdType": hold_type,
                    "coolHoldTemp": int(cool_temp * 10),
                    "heatHoldTemp": int(heat_temp * 10),
                    "fan": "on"
                }}]}
        log_msg_action = "set hold temp by ID with fan on"
        return self.make_request(body, log_msg_action)

    def set_climate_hold(self, id, climate, hold_type="nextTransition"):
        ''' Set a climate hold - ie away, home, sleep '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "setHold", "params": {
                    "holdType": hold_type,
                    "holdClimateRef": climate
                }}]}
        log_msg_action = "set climate hold"
        return self.make_request(body, log_msg_action)

    def delete_vacation(self, id, vacation):
        ''' Delete the vacation with name vacation '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "deleteVacation", "params": {
                    "name": vacation
                }}]}

        log_msg_action = "delete a vacation"
        return self.make_request(body, log_msg_action)

    def resume_program(self, id, resume_all=False):
        ''' Resume currently scheduled program '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "resumeProgram", "params": {
                    "resumeAll": resume_all
                }}]}

        log_msg_action = "resume program"
        return self.make_request(body, log_msg_action)

    def send_message(self, id, message="Hello from python-ecobee!"):
        ''' Send a message to the thermostat '''
        body = {"selection": {
                    "selectionType": "thermostats",
                    "selectionMatch": id },
                "functions": [{"type": "sendMessage", "params": {
                    "text": message[0:500]
                }}]}

        log_msg_action = "send message"
        return self.make_request(body, log_msg_action)

    def set_humidity(self, id, humidity):
        ''' Set humidity level'''
        body = {"selection": {"selectionType": "thermostats",
                              "selectionMatch": id },
                              "thermostat": {
                                  "settings": {
                                      "humidity": int(humidity)
                                  }
                              }}

        log_msg_action = "set humidity level"
        return self.make_request(body, log_msg_action)
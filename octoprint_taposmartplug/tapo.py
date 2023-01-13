import logging

from PyP100 import PyP100, PyP110
from octoprint_taposmartplug.utils import decode_string
from .settings import PlugSettings, PlugType

class TapoPlugAdapter:
	def __init__(self, ip, username, password , plug_type=PlugType.P100):
		self._ip = ip
		self._type = plug_type
		self._tapo_plug = PyP100.P100(ip, username, password)

		if plug_type == PlugType.P110:
			self._tapo_plug = PyP110.P110(ip, username, password)

		self._tapo_plug.handshake()  # Creates the cookies required for further methods
		self._tapo_plug.login()  # Sends credentials to the plug and creates AES Key and IV for further methods
		self._logger = logging.getLogger("octoprint.plugins.taposmartplug.TapoPlugAdapter")

	def send_turn_on(self):
		self._logger.info("%s - Turning on." % self._ip)
		self._tapo_plug.turnOn()  # Sends the turn on request
		return self.get_status()

	@property
	def ip(self):
		return self._ip

	@property
	def type(self):
		return self._type

	@staticmethod
	def plug_search(lst, key, value):
		for item in lst:
			if item[key] == value.strip():
				return item

	def get_status(self):
		self._logger.debug("%s - Checking status" % self._ip)
		response = self._tapo_plug.getDeviceInfo()
		status = self.lookup(response, *["result", "device_on"])
		self._logger.debug("%s - Status: %d".format(self._ip, status))

		if status == 1:
			return dict(currentState="on", ip=self._ip)
		elif status == 0:
			return dict(currentState="off", ip=self._ip)
		else:
			self._logger.debug(response)
			return dict(currentState="unknown", ip=self._ip)

	def lookup(self, dic, key, *keys):
		if keys:
			return self.lookup(dic.get(key, {}), *keys)
		return dic.get(key)

	def send_turn_off(self):
		self._logger.info("%s - Turning off." % self._ip)
		self._tapo_plug.turnOff()
		return self.get_status()

	def get_energy(self):
		if self._type == PlugType.P110:
			return self._tapo_plug.getEnergyUsage()
		else:
			return {}

	@staticmethod
	def create_tapo_plug_adapter(p: dict):
		return TapoPlugAdapter(
				p[PlugSettings.ip],
				p[PlugSettings.username],
				decode_string(p[PlugSettings.password]),
				p[PlugSettings.type]
		)

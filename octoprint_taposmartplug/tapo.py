import logging

from PyP100 import PyP100

from .utils import decode_string
from .settings import PlugSettings


class TapoP100Adapter:
	def __init__(self, ip, username, password):
		self._ip = ip
		# Creating a P100 plug object
		self._p100 = PyP100.P100(ip, username, password)
		self._p100.handshake()  # Creates the cookies required for further methods
		self._p100.login()  # Sends credentials to the plug and creates AES Key and IV for further methods
		self._logger = logging.getLogger("octoprint.plugins.taposmartplug.TapoAdapter")

	def send_turn_on(self):
		self._logger.info("%s - Turning on." % self._ip)
		self._p100.turnOn()  # Sends the turn on request
		return self.get_status()

	@property
	def ip(self):
		return self._ip

	@staticmethod
	def plug_search(lst, key, value):
		for item in lst:
			if item[key] == value.strip():
				return item

	def get_status(self):
		self._logger.debug("%s - Checking status" % self._ip)
		response = self._p100.getDeviceInfo()
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
		self._p100.turnOff()
		return self.get_status()

	@staticmethod
	def create_tapo_p100_adapter(p: dict):
		return TapoP100Adapter(
				p[PlugSettings.ip],
				p[PlugSettings.username],
				decode_string(p[PlugSettings.password])
		)

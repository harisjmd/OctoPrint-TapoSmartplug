import logging

from .tapo import TapoP100Adapter


class Commands:
	turnOn = "turnOn"
	turnOff = "turnOff"
	checkStatus = "checkStatus"
	enableAutomaticShutdown = "enableAutomaticShutdown"
	disableAutomaticShutdown = "disableAutomaticShutdown"
	abortAutomaticShutdown = "abortAutomaticShutdown"

	@staticmethod
	def get_available_commands():
		return {
			Commands.turnOn:                   ["ip"],
			Commands.turnOff:                  ["ip"],
			Commands.checkStatus:              ["ip"],
			Commands.enableAutomaticShutdown:  [],
			Commands.disableAutomaticShutdown: [],
			Commands.abortAutomaticShutdown:   [],
		}

	@staticmethod
	def get_auto_shutdown_cmds():
		return [
			Commands.enableAutomaticShutdown,
			Commands.disableAutomaticShutdown,
			Commands.abortAutomaticShutdown,
		]


class TapoSmartPlugApi:
	def __init__(self, plugin):
		self.plugin = plugin
		self._logger = logging.getLogger("octoprint.plugins.taposmartplug.TapoSmartPlugApi")

	def on_api_get(self, tapo: TapoP100Adapter, request):
		self._logger.debug(request.args)
		if request.args.get(Commands.checkStatus):
			response = tapo.get_status()
			return response

	def on_api_command(self, tapo: TapoP100Adapter, command, data):
		if command == Commands.turnOn:
			return tapo.send_turn_on()
		elif command == Commands.turnOff:
			return tapo.send_turn_off()
		elif command == Commands.checkStatus:
			return tapo.get_status()
		elif command in Commands.get_auto_shutdown_cmds():
			return self.plugin.handle_auto_shutdown_cmd(tapo, command, data)

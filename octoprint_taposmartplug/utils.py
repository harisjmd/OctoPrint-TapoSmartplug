

def encode_string(s):
	import base64
	message_bytes = s.encode('ascii')
	base64_bytes = base64.b64encode(message_bytes)
	return base64_bytes.decode('ascii')


def decode_string(s):
	import base64
	base64_bytes = s.encode('ascii')
	message_bytes = base64.b64decode(base64_bytes)
	return message_bytes.decode('ascii')

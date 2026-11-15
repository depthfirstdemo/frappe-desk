# Copyright (c) 2019, Frappe Technologies and contributors
# License: MIT. See LICENSE

import datetime
import ipaddress
import json
import socket
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

import frappe
from frappe import _
from frappe.utils import get_request_session


class _PinnedHostnameAdapter(HTTPAdapter):
	"""Connect to a checked IP while verifying the original HTTPS hostname."""

	def __init__(self, hostname):
		self.hostname = hostname
		super().__init__()

	def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
		pool_kwargs.update(assert_hostname=self.hostname, server_hostname=self.hostname)
		super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


def make_safe_get_request(url):
	"""Fetch a public URL without proxies, DNS rebinding or redirect-based SSRF."""
	try:
		parsed = urlsplit(url)
		if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None:
			raise ValueError
		if parsed.password is not None or "%" in parsed.hostname:
			raise ValueError
		port = parsed.port  # Also reject malformed ports before resolving the host.
		if port == 0:
			raise ValueError
		hostname = parsed.hostname.encode("idna").decode("ascii")
	except (TypeError, ValueError, UnicodeError):
		frappe.throw(_("Invalid OpenID Configuration URL"))

	addresses = {
		ipaddress.ip_address(result[4][0])
		for result in socket.getaddrinfo(
			hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
		)
	}
	if not addresses or any(not address.is_global or address.is_reserved for address in addresses):
		frappe.throw(_("OpenID Configuration URL must resolve to a public IP address"))

	# Make the request to the vetted address, not the hostname (which could resolve differently later).
	address = next(iter(addresses))
	ip_host = f"[{address}]" if address.version == 6 else str(address)
	url_host = f"{ip_host}:{port}" if port is not None else ip_host
	pinned_url = urlunsplit((parsed.scheme, url_host, parsed.path, parsed.query, ""))
	host_header = f"[{hostname}]" if ":" in hostname else hostname
	if port is not None:
		host_header = f"{host_header}:{port}"

	with requests.Session() as session:
		session.trust_env = False  # Do not route through environment-configured proxies.
		if parsed.scheme == "https":
			session.mount("https://", _PinnedHostnameAdapter(hostname))
		response = frappe.flags.integration_request = session.get(
			pinned_url, headers={"Host": host_header}, allow_redirects=False, timeout=10
		)
		# Redirects must not bypass the IP check, or expose an internal Location response.
		if 300 <= response.status_code < 400:
			frappe.throw(_("OpenID Configuration URL must not redirect"))
		response.raise_for_status()
		return _parse_response(response)


def make_request(method, url, auth=None, headers=None, data=None, json=None, params=None):
	auth = auth or ""
	data = data or {}
	headers = headers or {}

	try:
		s = get_request_session()
		response = frappe.flags.integration_request = s.request(
			method, url, data=data, auth=auth, headers=headers, json=json, params=params
		)
		response.raise_for_status()

		return _parse_response(response)
	except Exception as exc:
		frappe.log_error()
		raise exc


def _parse_response(response):
	# Check whether the response has a content-type, before trying to check what it is
	if content_type := response.headers.get("content-type"):
		if content_type == "text/plain; charset=utf-8":
			return parse_qs(response.text)
		elif content_type.startswith("application/") and content_type.split(";")[0].endswith("json"):
			return response.json()
		elif response.text:
			return response.text


def make_get_request(url, **kwargs):
	return make_request("GET", url, **kwargs)


def make_post_request(url, **kwargs):
	return make_request("POST", url, **kwargs)


def make_put_request(url, **kwargs):
	return make_request("PUT", url, **kwargs)


def make_patch_request(url, **kwargs):
	return make_request("PATCH", url, **kwargs)


def make_delete_request(url, **kwargs):
	return make_request("DELETE", url, **kwargs)


def create_request_log(
	data,
	integration_type=None,
	service_name=None,
	name=None,
	error=None,
	request_headers=None,
	output=None,
	**kwargs,
):
	"""
	DEPRECATED: The parameter integration_type will be removed in the next major release.
	Use is_remote_request instead.
	"""
	if integration_type == "Remote":
		kwargs["is_remote_request"] = 1

	elif integration_type == "Subscription Notification":
		kwargs["request_description"] = integration_type

	reference_doctype = reference_docname = None
	if "reference_doctype" not in kwargs:
		if isinstance(data, str):
			data = json.loads(data)

		reference_doctype = data.get("reference_doctype")
		reference_docname = data.get("reference_docname")

	integration_request = frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": service_name,
			"request_headers": get_json(request_headers),
			"data": get_json(data),
			"output": get_json(output),
			"error": get_json(error),
			"reference_doctype": reference_doctype,
			"reference_docname": reference_docname,
			**kwargs,
		}
	)

	if name:
		integration_request.flags._name = name

	integration_request.insert(ignore_permissions=True)
	frappe.db.commit()

	return integration_request


def get_json(obj):
	return obj if isinstance(obj, str) else frappe.as_json(obj, indent=1)


def json_handler(obj):
	if isinstance(obj, datetime.date | datetime.timedelta | datetime.datetime):
		return str(obj)

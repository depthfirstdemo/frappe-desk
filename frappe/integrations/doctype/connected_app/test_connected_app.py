# Copyright (c) 2019, Frappe Technologies and contributors
# License: MIT. See LICENSE
from unittest.mock import patch
from urllib.parse import urljoin

import requests

import frappe
from frappe.integrations.utils import _PinnedHostnameAdapter, make_safe_get_request
from frappe.integrations.doctype.social_login_key.test_social_login_key import (
	create_or_update_social_login_key,
)
from frappe.tests.utils import FrappeTestCase


def get_user(usr, pwd):
	user = frappe.new_doc("User")
	user.email = usr
	user.enabled = 1
	user.first_name = "_Test"
	user.new_password = pwd
	user.roles = []
	user.append("roles", {"doctype": "Has Role", "parentfield": "roles", "role": "System Manager"})
	user.insert()

	return user


def get_connected_app():
	doctype = "Connected App"
	connected_app = frappe.new_doc(doctype)
	connected_app.provider_name = "frappe"
	connected_app.scopes = []
	connected_app.append("scopes", {"scope": "all"})
	connected_app.insert()

	return connected_app


def get_oauth_client():
	oauth_client = frappe.new_doc("OAuth Client")
	oauth_client.app_name = "_Test Connected App"
	oauth_client.redirect_uris = "to be replaced"
	oauth_client.default_redirect_uri = "to be replaced"
	oauth_client.grant_type = "Authorization Code"
	oauth_client.response_type = "Code"
	oauth_client.skip_authorization = 1
	oauth_client.insert()

	return oauth_client


class TestSafeOpenIDConfiguration(FrappeTestCase):
	def test_private_addresses_and_invalid_schemes(self):
		for url in (
			"http://169.254.169.254/latest/meta-data/",
			"http://127.0.0.1/",
			"http://[::1]/",
			"file:///etc/passwd",
			"http://user@8.8.8.8/",
		):
			with self.subTest(url=url), self.assertRaises(frappe.ValidationError):
				make_safe_get_request(url)

	@patch("frappe.integrations.utils.requests.Session")
	@patch("frappe.integrations.utils.socket.getaddrinfo")
	def test_mixed_dns_results_are_rejected(self, resolve, session):
		resolve.return_value = [
			(None, None, None, None, ("8.8.8.8", 443)),
			(None, None, None, None, ("10.0.0.1", 443)),
		]
		with self.assertRaises(frappe.ValidationError):
			make_safe_get_request("https://example.com/.well-known/openid-configuration")
		session.assert_not_called()

	@patch("frappe.integrations.utils.requests.Session")
	@patch("frappe.integrations.utils.socket.getaddrinfo")
	def test_public_url_is_pinned_and_redirects_are_rejected(self, resolve, session_factory):
		resolve.return_value = [(None, None, None, None, ("8.8.8.8", 443))]
		session = session_factory.return_value
		session.__enter__.return_value = session
		response = session.get.return_value
		response.status_code = 200
		response.headers = {"content-type": "application/json"}
		response.json.return_value = {"issuer": "https://example.com"}
		url = "https://example.com/.well-known/openid-configuration"

		self.assertEqual(make_safe_get_request(url), {"issuer": "https://example.com"})
		session.get.assert_called_once_with(
			"https://8.8.8.8/.well-known/openid-configuration",
			headers={"Host": "example.com"},
			allow_redirects=False,
			timeout=10,
		)
		self.assertFalse(session.trust_env)
		adapter = session.mount.call_args.args[1]
		self.assertIsInstance(adapter, _PinnedHostnameAdapter)
		self.assertEqual(adapter.poolmanager.connection_pool_kw["assert_hostname"], "example.com")
		self.assertEqual(adapter.poolmanager.connection_pool_kw["server_hostname"], "example.com")

		response.status_code = 302
		with self.assertRaises(frappe.ValidationError):
			make_safe_get_request(url)


class TestConnectedApp(FrappeTestCase):
	def setUp(self):
		"""Set up a Connected App that connects to our own oAuth provider.

		Frappe comes with it's own oAuth2 provider that we can test against. The
		client credentials can be obtained from an "OAuth Client". All depends
		on "Social Login Key" so we create one as well.

		The redirect URIs from "Connected App" and "OAuth Client" have to match.
		Frappe's "Authorization URL" and "Access Token URL" (actually they're
		just endpoints) are stored in "Social Login Key" so we get them from
		there.
		"""
		self.user_name = "test-connected-app@example.com"
		self.user_password = "Eastern_43A1W"

		self.user = get_user(self.user_name, self.user_password)
		self.connected_app = get_connected_app()
		self.oauth_client = get_oauth_client()
		social_login_key = create_or_update_social_login_key()
		self.base_url = social_login_key.get("base_url")

		frappe.db.commit()
		self.connected_app.reload()
		self.oauth_client.reload()

		redirect_uri = self.connected_app.get("redirect_uri")
		self.oauth_client.update({"redirect_uris": redirect_uri, "default_redirect_uri": redirect_uri})
		self.oauth_client.save()

		self.connected_app.update(
			{
				"authorization_uri": urljoin(self.base_url, social_login_key.get("authorize_url")),
				"client_id": self.oauth_client.get("client_id"),
				"client_secret": self.oauth_client.get("client_secret"),
				"token_uri": urljoin(self.base_url, social_login_key.get("access_token_url")),
			}
		)
		self.connected_app.save()

		frappe.db.commit()
		self.connected_app.reload()
		self.oauth_client.reload()

	def test_web_application_flow(self):
		"""Simulate a logged in user who opens the authorization URL."""

		def login():
			return session.get(
				urljoin(self.base_url, "/api/method/login"),
				params={"usr": self.user_name, "pwd": self.user_password},
			)

		session = requests.Session()

		first_login = login()
		self.assertEqual(first_login.status_code, 200)

		authorization_url = self.connected_app.initiate_web_application_flow(user=self.user_name)

		auth_response = session.get(authorization_url)
		self.assertEqual(auth_response.status_code, 200)

		callback_response = session.get(auth_response.url)
		self.assertEqual(callback_response.status_code, 200)

		self.token_cache = self.connected_app.get_token_cache(self.user_name)
		token = self.token_cache.get_password("access_token")
		self.assertNotEqual(token, None)

		oauth2_session = self.connected_app.get_oauth2_session(self.user_name)
		resp = oauth2_session.get(urljoin(self.base_url, "/api/method/frappe.auth.get_logged_user"))
		self.assertEqual(resp.json().get("message"), self.user_name)

	def tearDown(self):
		def delete_if_exists(attribute):
			doc = getattr(self, attribute, None)
			if doc:
				doc.delete(force=True)

		frappe.db.commit()  # Avoid snapshot violation issues

		delete_if_exists("token_cache")
		delete_if_exists("connected_app")

		if getattr(self, "oauth_client", None):
			tokens = frappe.get_all("OAuth Bearer Token", filters={"client": self.oauth_client.name})
			for token in tokens:
				doc = frappe.get_doc("OAuth Bearer Token", token.name)
				doc.delete()

			codes = frappe.get_all("OAuth Authorization Code", filters={"client": self.oauth_client.name})
			for code in codes:
				doc = frappe.get_doc("OAuth Authorization Code", code.name)
				doc.delete()

		delete_if_exists("user")
		delete_if_exists("oauth_client")

		frappe.db.commit()

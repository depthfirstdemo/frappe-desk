# Copyright (c) 2020, Frappe Technologies and Contributors
# License: MIT. See LICENSE
from unittest.mock import patch

import frappe
from frappe.desk.desktop import update_onboarding_step
from frappe.tests.utils import FrappeTestCase


class TestOnboardingStep(FrappeTestCase):
	def test_update_onboarding_step_rejects_other_fields(self):
		with patch("frappe.db.set_value") as set_value:
			for field in ("action_label", "title", "intro_video_url"):
				with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
					update_onboarding_step("Test Step", field, "<img src=x onerror=alert(1)>")
			set_value.assert_not_called()

	def test_update_onboarding_step_allows_status_fields(self):
		with patch("frappe.db.set_value") as set_value, patch("frappe.utils.telemetry.capture"):
			for field, value in (("is_complete", 1), ("is_skipped", 0)):
				with self.subTest(field=field):
					update_onboarding_step("Test Step", field, value)
					set_value.assert_called_with("Onboarding Step", "Test Step", field, value)
			self.assertEqual(set_value.call_count, 2)

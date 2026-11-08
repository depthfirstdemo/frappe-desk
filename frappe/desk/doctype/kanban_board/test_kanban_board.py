# Copyright (c) 2015, Frappe Technologies and Contributors
# License: MIT. See LICENSE

import frappe
from frappe.tests.utils import FrappeTestCase


class TestKanbanBoard(FrappeTestCase):
	def test_column_names_cannot_inject_html(self):
		for title in ('<img src=x onerror=alert(1)>', '" onmouseover="alert(1)'):
			with self.subTest(title=title):
				board = frappe.new_doc("Kanban Board")
				board.append("columns", {"column_name": title})
				with self.assertRaises(frappe.ValidationError):
					board.validate()

	def test_plain_text_column_names_are_allowed(self):
		board = frappe.new_doc("Kanban Board")
		board.append("columns", {"column_name": "John's R&D tasks", "indicator": "Light Blue"})
		board.validate()

	def test_indicator_cannot_inject_html_or_css(self):
		for indicator in ('Red" onmouseover="alert(1)', "Red);background:url(x)"):
			with self.subTest(indicator=indicator):
				board = frappe.new_doc("Kanban Board")
				board.append("columns", {"column_name": "Open", "indicator": indicator})
				with self.assertRaises(frappe.ValidationError):
					board.validate()

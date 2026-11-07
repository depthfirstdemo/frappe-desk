const cookie = require("cookie");
const request = require("superagent");
const { get_url } = require("../utils");

function authenticate_with_frappe(socket, next) {
	// The namespace determines the site for both authentication and room membership.
	const site = socket.nsp.name.slice(1); // remove leading `/`
	if (!site) {
		next(new Error("Invalid namespace"));
		return;
	}
	socket.site_name = site;

	if (get_hostname(socket.request.headers.host) != get_hostname(socket.request.headers.origin)) {
		next(new Error("Invalid origin"));
		return;
	}

	if (!socket.request.headers.cookie && !socket.request.headers.authorization) {
		next(
			new Error(
				"Missing cookie and authorization header. Either one needed for authentication."
			)
		);
		return;
	}

	let cookies = cookie.parse(socket.request.headers.cookie || "");
	let authorization_header = socket.request.headers.authorization;

	if (!cookies.sid && !authorization_header) {
		next(new Error("No authentication method used. Use cookie or authorization header."));
		return;
	}

	let auth_req = request
		.get(get_url(socket, "/api/method/frappe.realtime.get_user_info"))
		.set("X-Frappe-Site-Name", site);
	if (authorization_header) {
		auth_req = auth_req.set("Authorization", authorization_header);
	} else if (cookies.sid) {
		auth_req = auth_req.query({ sid: cookies.sid });
	}

	auth_req
		.type("form")
		.then((res) => {
			socket.user = res.body.message.user;
			socket.user_type = res.body.message.user_type;
			socket.sid = cookies.sid;
			socket.authorization_header = authorization_header;
			next();
		})
		.catch((e) => {
			next(new Error(`Unauthorized: ${e}`));
		});
}

function get_hostname(url) {
	if (!url) return undefined;
	if (url.indexOf("://") > -1) {
		url = url.split("/")[2];
	}
	return url.match(/:/g) ? url.slice(0, url.indexOf(":")) : url;
}

module.exports = authenticate_with_frappe;

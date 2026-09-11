
""" commentary
- ALL our HTTP details will stay inside here
- AS of 11092026
	> the Login function has its own post request
	> the dedicated POST function is for other POST requests
"""

import requests

class APIClient:

	""" our object here is the thing module 4 uses to communicate with module 2
	"""

	def __init__(self, base_url):
		self.base_url = base_url.rstrip("/")
		self.token = None


	def set_token(self, token):

		""" token parameter is the JWT returned by module 2 after login
		"""

		self.token = token


	def login(self, email, password):
		response = requests.post(
			f"{self.base_url}/api/v1/auth/login",
			json={
				"email": email,
				"password": password,
			}
		)
		response.raise_for_status()

		data = response.json()
		self.token = data["access_token"]

		return data


	def get(self, endpoint):
		headers = {}

		if self.token:
			headers["authorization"] = f"bearer {self.token}"

		response = requests.get(
			f"{self.base_url}{endpoint}",
			headers=headers
		)
		response.raise_for_status()

		return response.json()  # this converts the resposne into PY data so we can work on it :)


	def get_session(self, session_id):
		return self.get(f"/api/v1/sessions/{session_id}")


	def get_sessions(self):
		return self.get("/api/v1/sessions/")


	def get_checkins(self):
		return self.get("/api/v1/checkins/")


    def get_session_checkins(self, session_id):
        return self.get(f"/api/v1/checkins/session/{session_id}")


    def get_flagged_checkins(self):
        return self.get("/api/v1/checkins/flagged")


    def get_overview_stats(self):
        return self.get("/api/v1/stats/overview")


    def get_session_stats(self, session_id):
        return self.get(f"/api/v1/stats/sessions/{session_id}")


    def get_course_stats(self, course_id):
        return self.get(f"/api/v1/stats/courses/{course_id}")


    def get_student_stats(self, student_id):
        return self.get(f"/api/v1/stats/students/{student_id}")


    def get_audit_logs(self):
        return self.get("/api/v1/audit/")


	def post(self, endpoint, data=None):

		""" data parameter is the information we are sending to the backend , however ,
		it is optional
		"""

		headers = {}

		if self.token:
			headers["authorization"] = f"bearer {self.token}"  # TODO - not sure what this does

		response = requests.post(
			f"{self.base_url}{endpoint}",
			json = data,
			headers = headers
		)
		response.raise_for_status()

		return response.json()


	def create_session(self, data):
		return self.post("/api/v1/sessions/", data)


	def download(self, endpoint):
		headers = {}

		if self.token:
			headers["authorization"] = f"bearer {self.token}"

		response = requests.get(
			f"{self.base_url}{endpoint}",
			headers = headers
		)
		response.raise_for_status()

        return response.content  # we want the actual bytes of the response if we using CSV


    def export_attendance(self, course_id):
        return self.download(f"/api/v1/export/attendance/{course_id}")


    def export_session(self, session_id):
        return self.download(f"/api/v1/export/session/{session_id}")

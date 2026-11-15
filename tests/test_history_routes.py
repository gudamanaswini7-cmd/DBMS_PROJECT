import unittest

from app import app


class HistoryRoutesTest(unittest.TestCase):
    def test_citizen_history_route_exists(self):
        client = app.test_client()
        response = client.get('/citizen/history')
        self.assertIn(response.status_code, (200, 302))

    def test_staff_history_route_exists(self):
        client = app.test_client()
        response = client.get('/staff/history')
        self.assertIn(response.status_code, (200, 302))


if __name__ == '__main__':
    unittest.main()

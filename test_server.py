import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class RoutingTests(unittest.TestCase):
    def test_route_avoids_obstacles_and_is_contiguous(self):
        scenario = server.build_scenario()
        result = server.find_route({"start": scenario["start"], "goal": scenario["goal"], "riskWeight": 4}, scenario)
        self.assertGreater(len(result["path"]), 2)
        self.assertEqual(result["path"][0], [2, 16])
        self.assertEqual(result["path"][-1], [27, 3])
        for a, b in zip(result["path"], result["path"][1:]):
            self.assertLessEqual(max(abs(a[0]-b[0]), abs(a[1]-b[1])), 1)
            self.assertFalse(scenario["layers"]["blocked"][b[1]][b[0]])
        self.assertGreater(result["distanceMeters"], 0)

    def test_zero_length_route(self):
        result = server.find_route({"start":{"x":1,"y":1},"goal":{"x":1,"y":1}})
        self.assertEqual(result["distanceMeters"], 0)
        self.assertEqual(result["path"], [[1,1]])

    def test_validation(self):
        cases = [({}, "start must"), ({"start":{"x":-1,"y":0},"goal":{"x":1,"y":1}}, "outside"),
                 ({"start":{"x":13,"y":8},"goal":{"x":1,"y":1}}, "traversable"),
                 ({"start":{"x":1,"y":1},"goal":{"x":2,"y":2},"riskWeight":99}, "between 0 and 20")]
        for payload, message in cases:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, message):
                server.find_route(payload)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close(); cls.thread.join()

    def test_scenario_and_geojson_endpoints(self):
        with urlopen(self.base + "/api/scenario") as response:
            scenario=json.load(response)
            self.assertEqual(response.status,200)
            self.assertEqual(scenario["width"],30)
        with urlopen(self.base + "/api/scenario/geojson") as response:
            geo=json.load(response)
            self.assertEqual(geo["type"],"FeatureCollection")
            self.assertEqual(len(geo["features"]),600)

    def test_frontend_is_served_locally(self):
        with urlopen(self.base + "/") as response:
            html=response.read().decode()
            self.assertEqual(response.status,200)
            self.assertIn("3D TERRAIN VIEW",html)
        with urlopen(self.base + "/static/app.js") as response:
            self.assertIn("javascript",response.headers.get("Content-Type"))

    def test_route_api_and_bad_json(self):
        data=json.dumps({"start":{"x":2,"y":16},"goal":{"x":27,"y":3}}).encode()
        req=Request(self.base+"/api/route",data=data,headers={"Content-Type":"application/json"},method="POST")
        with urlopen(req) as response:
            self.assertIn("path",json.load(response))
        bad=Request(self.base+"/api/route",data=b"{",headers={"Content-Type":"application/json"},method="POST")
        with self.assertRaises(HTTPError) as exc: urlopen(bad)
        self.assertEqual(exc.exception.code,400)


if __name__ == "__main__":
    unittest.main()

"""Focused offline safety and request/response contract checks; no live service required."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

import httpx2 as httpx

from bridge import Bridge, BridgeError, INLINE_BYTES, form_parts


def catalog():
    binary = {"type": "string", "format": "binary"}
    return {"openapi": "3.0.3", "paths": {
        "/api/v1/test/merge": {"post": {"operationId": "merge", "summary": "Merge PDFs", "requestBody": {
            "content": {"multipart/form-data": {"schema": {"$ref": "#/components/schemas/Merge"}}}}}},
        "/api/v1/test/json": {"post": {"operationId": "jsonOperation", "requestBody": {
            "content": {"application/json": {"schema": {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}}}}}},
        "/api/v1/job/{jobId}": {"get": {"operationId": "job", "parameters": [
            {"in": "path", "name": "jobId", "required": True, "schema": {"type": "string"}},
            {"in": "query", "name": "verbose", "schema": {"type": "boolean"}}]}},
    }, "components": {"schemas": {"Merge": {"type": "object", "required": ["fileInput", "removeCertSign"], "properties": {
        "fileInput": {"type": "array", "items": binary}, "removeCertSign": {"type": "boolean"},
        "password": {"type": "string", "format": "password"}, "languages": {"type": "array", "items": {"type": "string"}}
    }}}}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.pdf"
        self.input.write_bytes(b"%PDF-1.7 synthetic")
        self.requests = []
        self.response = lambda request: httpx.Response(200, content=b"%PDF-1.7 result", headers={"Content-Type": "application/pdf"})
        def handler(request):
            self.requests.append((request, request.read()))
            return self.response(request)
        self.bridge = Bridge("http://10.254.230.50:8080", str(self.root / "artifacts"),
                             document=catalog(), transport=httpx.MockTransport(handler))
        self.addCleanup(self.bridge.client.close)

    def merge(self, **kwargs):
        args = {"operation_id": "merge", "parameters": {"removeCertSign": False}, "files": {"fileInput": [str(self.input)]}}
        args.update(kwargs)
        return self.bridge.execute(**args)

    def test_discovery_pagination_exact_schema(self):
        self.assertEqual(self.bridge.operations("merge")["matching_operations"], 1)
        self.assertEqual(self.bridge.operations(limit=1)["next_offset"], 1)
        schema = self.bridge.operation_schema("merge")
        self.assertEqual(schema["components"]["schemas"]["Merge"], catalog()["components"]["schemas"]["Merge"])

    def test_multipart_multiple_files_and_explicit_false(self):
        second = self.root / "second.pdf"
        second.write_bytes(b"%PDF second")
        output = self.merge(files={"fileInput": [str(self.input), str(second)]},
                            parameters={"removeCertSign": False, "languages": ["eng", "deu"]})
        request, body = self.requests[-1]
        self.assertEqual(request.url.host, "10.254.230.50")
        self.assertEqual(body.count(b'name="fileInput"'), 2)
        self.assertIn(b"Content-Type: application/pdf", body)
        self.assertLess(body.index(b"input.pdf"), body.index(b"second.pdf"))
        self.assertIn(b"\r\nfalse\r\n", body)
        self.assertEqual(body.count(b'name="languages"'), 2)
        self.assertTrue(Path(output["artifact"]["path"]).is_file())

    def test_no_default_application_invalid_type_and_unknown_field(self):
        for params in ({}, {"removeCertSign": "false"}, {"removeCertSign": False, "extra": 1}):
            with self.assertRaises(BridgeError):
                self.merge(parameters=params)
        self.assertFalse(self.requests)

    def test_binary_fields_cannot_be_text(self):
        with self.assertRaises(BridgeError):
            self.merge(parameters={"removeCertSign": False, "fileInput": ["pretend.pdf"]}, files={})
        self.assertFalse(self.requests)

    def test_oneof_file_upload_or_reference_schema(self):
        schemas = self.bridge._document["components"]["schemas"]
        schemas["Merge"] = {"oneOf": [
            {"type": "object", "properties": {"fileInput": {"type": "string", "format": "binary"}}, "required": ["fileInput"]},
            {"type": "object", "properties": {"fileId": {"type": "string"}}, "required": ["fileId"]}]}
        self.merge(parameters={}, files={"fileInput": [str(self.input)]})
        self.merge(parameters={"fileId": "server-file-id"}, files={})
        with self.assertRaises(BridgeError):
            self.merge(parameters={"fileId": "server-file-id"}, files={"fileInput": [str(self.input)]})

    def test_no_redirects_or_error_reflection(self):
        self.response = lambda request: httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}, content=b"secret-password")
        with self.assertRaises(BridgeError) as caught:
            self.merge()
        self.assertNotIn("secret-password", str(caught.exception))
        self.assertEqual(len(self.requests), 1)

    def test_host_cannot_be_overridden_through_path_or_operation(self):
        for value in ("../secret", "//example.org", "a\\b", "..", "a\n"):
            with self.assertRaises(BridgeError):
                self.bridge.execute("job", path_parameters={"jobId": value})
        with self.assertRaises(BridgeError):
            self.bridge.execute("https://example.org/api")
        self.assertFalse(self.requests)
        self.bridge.execute("job", path_parameters={"jobId": "id?x=1"}, query_parameters={"verbose": False})
        self.assertEqual(self.requests[-1][0].url.host, "10.254.230.50")
        self.assertIn(b"id%3Fx%3D1", self.requests[-1][0].url.raw_path)

    def test_async_query_and_known_query_only(self):
        self.merge(asynchronous=True)
        self.assertEqual(self.requests[-1][0].url.params["async"], "true")
        with self.assertRaises(BridgeError):
            self.bridge.execute("job", path_parameters={"jobId": "a"}, query_parameters={"url": "http://example.org"})

    def test_symlink_and_special_file_input_rejected(self):
        linked = self.root / "linked.pdf"
        linked.symlink_to(self.input)
        for name in (str(linked), "/dev/null"):
            with self.assertRaises(BridgeError):
                self.merge(files={"fileInput": [name]})
        self.assertFalse(self.requests)

    def test_secret_file_permission_and_no_value_in_errors(self):
        secret = self.root / "password"
        secret.write_text("PRIVATE-SECRET\n")
        secret.chmod(0o644)
        with self.assertRaises(BridgeError):
            self.merge(secret_files={"password": str(secret)})
        secret.chmod(0o600)
        self.merge(secret_files={"password": str(secret)})
        self.assertIn(b"PRIVATE-SECRET\r\n", self.requests[-1][1])
        with self.assertRaises(BridgeError) as caught:
            self.merge(parameters={"removeCertSign": "PRIVATE-SECRET"})
        self.assertNotIn("PRIVATE-SECRET", str(caught.exception))

    def test_json_file_input(self):
        source = self.root / "body.json"
        source.write_text('{"value":"document content"}')
        self.bridge.execute("jsonOperation", json_body_file=str(source))
        self.assertEqual(json.loads(self.requests[-1][1]), {"value": "document content"})

    def test_unique_private_outputs_and_unsafe_filename(self):
        self.response = lambda request: httpx.Response(200, content=b"%PDF result", headers={
            "Content-Type": "application/octet-stream", "Content-Disposition": 'attachment; filename="../../input.pdf"'})
        first = Path(self.merge()["artifact"]["path"])
        second = Path(self.merge()["artifact"]["path"])
        self.assertNotEqual(first, second)
        self.assertEqual(first.name, "result.pdf")
        self.assertTrue(first.is_relative_to(self.root / "artifacts"))
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        self.assertEqual(first.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.input.read_bytes(), b"%PDF-1.7 synthetic")

    def test_artifact_symlink_rejected(self):
        (self.root / "artifacts").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(BridgeError):
            self.merge()
        self.assertFalse((self.root / "result.pdf").exists())

    def test_response_limit_removes_partial(self):
        class Chunks(httpx.SyncByteStream):
            def __iter__(self):
                yield b"12345"
                yield b"67890"
        self.bridge.max_output_bytes = 8
        self.response = lambda request: httpx.Response(200, stream=Chunks(), headers={"Content-Type": "application/pdf"})
        with self.assertRaises(BridgeError):
            self.merge()
        self.assertEqual(list((self.root / "artifacts").iterdir()), [])

    def test_large_json_is_artifact_not_inline(self):
        self.response = lambda request: httpx.Response(200, json={"text": "x" * (INLINE_BYTES + 1)})
        result = self.merge()
        self.assertNotIn("json", result)
        self.assertGreater(result["artifact"]["bytes"], INLINE_BYTES)

    def test_external_schema_cannot_fetch(self):
        self.bridge._document["components"]["schemas"]["Merge"]["properties"]["password"] = {"$ref": "https://example.org/schema"}
        with self.assertRaises(BridgeError):
            self.merge()
        self.assertFalse(self.requests)

    def test_spring_nested_object_array_and_map_encoding(self):
        schema = {"type": "array", "items": {"type": "object", "properties": {"x": {"type": "number"}, "page": {"type": "integer"}}}}
        self.assertEqual(list(form_parts("redactions", [{"x": 1.5, "page": 1}], schema)),
                         [("redactions[0].x", "1.5"), ("redactions[0].page", "1")])
        self.assertEqual(list(form_parts("metadata", {"title": "test"}, {"type": "object", "additionalProperties": {"type": "string"}})),
                         [("metadata[title]", "test")])


if __name__ == "__main__":
    unittest.main()

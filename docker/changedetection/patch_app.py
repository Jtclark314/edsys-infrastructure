#!/usr/bin/env python3
"""Apply narrow fail-closed patches to the exact upstream 0.60.10 sources."""
from pathlib import Path


def replace(path, old, new, count=1):
    value = path.read_text()
    if value.count(old) != count:
        raise RuntimeError(f"Upstream source changed: {path.name}")
    path.write_text(value.replace(old, new))


def main():
    root = Path("/app/changedetectionio")
    requests = root / "content_fetchers/requests.py"
    replace(requests, "        self.proxy_override = proxy_override", "        from changedetectionio.edsys_guard import check_transport_override\n        check_transport_override(proxy_override)\n        self.proxy_override = proxy_override")
    replace(requests, "            ok, reason = is_fetch_url_allowed(url)", "            from changedetectionio.edsys_guard import check_watch_url\n            check_watch_url(url)\n            ok, reason = is_fetch_url_allowed(url)")
    replace(requests, "verify=False,", "verify=True,", 2)
    replace(requests, "                current_url = redirect_url", "                from changedetectionio.edsys_guard import check_redirect\n                check_watch_url(redirect_url)\n                check_redirect(current_url, redirect_url, request_headers)\n                current_url = redirect_url")
    replace(root / "content_fetchers/playwright.py", "        self.browser_type = os.getenv", "        from changedetectionio.edsys_guard import check_transport_override\n        check_transport_override(proxy_override, custom_browser_connection_url, browser=True)\n        self.browser_type = os.getenv")
    replace(root / "browser_steps/browser_steps.py", "            proxy=proxy,", "            proxy=__import__('changedetectionio.edsys_guard', fromlist=['browser_proxy']).browser_proxy(proxy),")
    for relative, headers, context, url in (
        ("content_fetchers/playwright.py", "request_headers", "context", "url"),
        ("browser_steps/browser_steps.py", "self.headers", "self.context", "self.start_url"),
    ):
        path = root / relative
        replace(path, "ignore_https_errors=True,", "ignore_https_errors=False,")
        replace(path, f"extra_http_headers={headers},", f"extra_http_headers=__import__('changedetectionio.edsys_guard', fromlist=['safe_headers']).safe_headers({headers}),")
        target = f"self.page = await {context}.new_page()"
        indent = "            " if context == "context" else "        "
        replace(path, target, f"{target}\n{indent}from changedetectionio.edsys_guard import scope_browser_headers\n{indent}await scope_browser_headers({context}, {headers}, {url}, self.page)")
    client = root / "llm/client.py"
    replace(client, "DEFAULT_RETRIES = 3", "DEFAULT_RETRIES = 1")
    replace(client, "            response = litellm.completion(**kwargs)", "            from changedetectionio.edsys_guard import local_completion\n            response = local_completion(litellm.completion, kwargs)")
    models = root / "llm/model_list.py"
    replace(models, "    api_base = api_base or None", "    from changedetectionio.edsys_guard import local_model_provider\n    local_model_provider(provider, api_base, api_key)\n    api_base = api_base or None")
    replace(models, "    return sorted(set(names))", "    import os\n    allowed = os.environ['EDSYS_LLM_MODEL']\n    installed = 'ollama/' + allowed.removeprefix('ollama_chat/')\n    return [allowed] if installed in names else []")
    replace(root / "model/__init__.py", "'llm_backend_profile': True,", "'llm_backend_profile': False,")
    replace(root / "forms.py", "llm_backend_profile = BooleanField(_l('AI enabled for this watch?'), default=True)",
            "llm_backend_profile = BooleanField(_l('AI enabled for this watch?'), default=False)")
    replace(root / "blueprint/ui/edit.py", """                        if not isinstance(config_value, dict):
                            continue""", """                        if not isinstance(config_value, dict):
                            target_field = getattr(form, f'processor_config_{config_key}', None)
                            if target_field is not None and not isinstance(target_field, FormField):
                                target_field.data = config_value
                            continue""")
    # Complete the upstream schema for the already installed visual processor.
    # Its native save path accepts processor_config_* fields and persists them
    # in image_ssim_diff.json; no new processor or privileged API route is added.
    import yaml
    spec_path = root.parent / "docs/api-spec.yaml"
    if not spec_path.is_file():
        spec_path = root / "docs/api-spec.yaml"
    spec = yaml.safe_load(spec_path.read_text())
    properties = spec["components"]["schemas"]["WatchBase"]["properties"]
    assert properties["processor"]["enum"] == ["restock_diff", "text_json_diff"]
    properties["processor"]["enum"].append("image_ssim_diff")
    assert properties["llm_backend_profile"]["default"] is True
    properties["llm_backend_profile"]["default"] = False
    properties["llm_backend_profile"]["description"] = properties["llm_backend_profile"]["description"].replace("default true", "default false")
    visual = {
        "processor_config_min_change_percentage": {"type": "integer", "minimum": 1, "maximum": 100},
        "processor_config_pixel_difference_threshold_sensitivity": {"type": "string", "enum": ["", "0", "20", "80", "200"]},
        "processor_config_bounding_box": {"type": "string", "maxLength": 100,
                                          "pattern": r"^(?:|(?:10000|[0-9]{1,4}),(?:10000|[0-9]{1,4}),(?:10000|[0-9]{1,4}),(?:10000|[0-9]{1,4}))$"},
        "processor_config_selection_mode": {"type": "string", "enum": ["", "element", "draw"]},
    }
    assert not set(visual).intersection(properties)
    properties.update(visual)
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))
    get_watch = root / "api/Watch.py"
    replace(get_watch, "        watch['processor_config_restock_diff_source'] = restock_source", """        watch['processor_config_restock_diff_source'] = restock_source
        if watch.get('processor') == 'image_ssim_diff':
            from changedetectionio.processors.base import difference_detection_processor
            visual = difference_detection_processor(self.datastore, uuid).get_extra_watch_config('image_ssim_diff.json')
            for key in ('min_change_percentage', 'pixel_difference_threshold_sensitivity', 'bounding_box', 'selection_mode'):
                if key in visual:
                    watch['processor_config_' + key] = visual[key]""")
    print("Qualified upstream transport/credential/local-AI patches applied")


if __name__ == "__main__":
    main()

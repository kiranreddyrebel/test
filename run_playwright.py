import asyncio
from playwright.async_api import async_playwright
import os
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()

        alerts = []
        console_messages = []
        network_requests_raw = []

        page.on("dialog", lambda dialog: (alerts.append(f"Dialog: {dialog.message} (URL: {dialog.page.url})"), asyncio.ensure_future(dialog.accept())))
        page.on("console", lambda msg: console_messages.append({"source": "main_page_or_outer_iframe", "type": msg.type, "text": msg.text, "location": msg.location}))

        def handle_request(request):
            network_requests_raw.append({
                "event_type": "request", "url": request.url, "method": request.method,
                "headers": request.headers, "frame_url": request.frame.url
            })
        page.on("request", handle_request)

        def handle_request_failed(request):
            failure_text_val = request.failure
            network_requests_raw.append({
                "event_type": "requestfailed", "url": request.url, "method": request.method,
                "failure_text": failure_text_val if failure_text_val else "N/A",
                "frame_url": request.frame.url
            })
        page.on("requestfailed", handle_request_failed)

        def handle_response(response):
            network_requests_raw.append({
                "event_type": "response", "url": response.url, "status": response.status,
                "headers": response.headers, "frame_url": response.frame.url
            })
        page.on("response", handle_response)

        # TARGET HTML FILE CHANGED HERE
        file_path = "file://" + os.path.join(os.getcwd(), "render_mixed_test_iframe.html")

        await page.goto(file_path, wait_until="networkidle")

        main_html_content = await page.content()

        outer_iframe_html = "Outer iframe not found or content not accessible."
        inner_iframes_html_list = []

        if len(page.frames) > 1:
            outer_iframe = page.frames[1]
            try:
                outer_iframe_html = await outer_iframe.content()
            except Exception as e:
                outer_iframe_html = f"Could not access outer iframe content: {str(e)}"
                console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get outer_iframe content: {e}", "location": {}})

            for i, inner_frame in enumerate(outer_iframe.child_frames): # Should be 6 inner frames
                iframe_name = f"Inner iframe {i+1} (src: {inner_frame.url})" # Use actual inner_frame.url for clarity
                try:
                    content = await inner_frame.content()
                    inner_iframes_html_list.append({"name": iframe_name, "content": content})
                except Exception as e:
                    content = f"Could not access content: {str(e)}"
                    inner_iframes_html_list.append({"name": iframe_name, "content": content})
                    console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get content for {iframe_name}: {e}", "location": {}})
        else:
            outer_iframe_html = "Outer iframe not found."

        await browser.close()

        # FILENAMES CHANGED HERE
        with open("mixed_test_alerts.txt", "w") as f:
            if alerts: f.writelines([a + "\n" for a in alerts])
            else: f.write("No alerts captured.\n")

        with open("mixed_test_console.json", "w") as f:
            json.dump(console_messages, f, indent=2)

        with open("mixed_test_dom.html", "w") as f:
            f.write("--- MAIN PAGE HTML ---\n")
            f.write(main_html_content)
            f.write("\n\n--- OUTER IFRAME (SRCDOC) DOCUMENT HTML ---\n")
            f.write(outer_iframe_html)
            for iframe_data in inner_iframes_html_list:
                f.write(f"\n\n--- {iframe_data['name']} DOCUMENT HTML ---\n")
                f.write(iframe_data['content'])

        with open("mixed_test_network.json", "w") as f:
            json.dump(network_requests_raw, f, indent=2)

        # Summary printouts
        print("--- ALERTS (saved to mixed_test_alerts.txt) ---")
        if not alerts: print("No alerts captured.")
        else:
            for alert in alerts: print(alert)

        print(f"\n--- CONSOLE MESSAGES (saved to mixed_test_console.json) ---")
        print(f"{len(console_messages)} console messages saved.")

        print(f"\n--- HTML CONTENT (saved to mixed_test_dom.html) ---")
        print(f"HTML content (main, outer iframe, {len(inner_iframes_html_list)} inner iframes) saved.")

        print(f"\n--- NETWORK ACTIVITY SUMMARY (details in mixed_test_network.json) ---")

        targets_of_interest = {
            "instance-data": "http://instance-data/latest/meta-data/",
            "localhost:443": "http://127.0.0.1:443/",
            "localhost:80": "http://127.0.0.1:80/",
            "gcp-token": "http://metadata.google.internal/computeMetadata/v1beta1/instance/service-accounts/default/token",
            "file-root": "file:///",
            "file-var-www": "file:///var/www/html/"
        }

        for name, url_substring in targets_of_interest.items():
            if "file://" in url_substring:
                # File access attempts are usually found in console, not as direct failed network requests
                console_errors_for_file = [msg for msg in console_messages if url_substring in msg.get("text","") or (msg.get("location",{}).get("url") == url_substring and msg.get("type") == "error")]
                # A more robust check would be to see if an iframe with this src failed to load its document.
                # This can be inferred if its content in mixed_test_dom.html is the error string.
                if any(url_substring in data['name'] and "Could not access content" in data['content'] for data in inner_iframes_html_list):
                     print(f"Attempt to load {name} ({url_substring}): Likely blocked (check DOM and console files for details).")
                elif console_errors_for_file:
                     print(f"Attempt to load {name} ({url_substring}): Found related console errors (check console file).")
                else:
                     print(f"Attempt to load {name} ({url_substring}): No specific failed network request or known console error pattern detected in summary.")

            else: # HTTP targets
                http_requests = [req for req in network_requests_raw if url_substring in req.get("url","")]
                successful_responses = [r for r in http_requests if r['event_type'] == 'response' and r.get('status') == 200]
                failed_requests = [r for r in http_requests if r['event_type'] == 'requestfailed']

                print(f"Target: {name} ({url_substring})")
                if successful_responses:
                    print(f"  Successful responses: {len(successful_responses)}")
                    for r in successful_responses: print(f"    - URL: {r['url']}, Status: {r['status']}")
                elif failed_requests:
                    print(f"  Failed requests: {len(failed_requests)}")
                    for r in failed_requests: print(f"    - URL: {r['url']}, Failure: {r['failure_text']}")
                else:
                    print(f"  No conclusive successful or failed network log entries for this target.")
                    other_related_events = [r for r in http_requests if r not in successful_responses and r not in failed_requests]
                    if other_related_events:
                        print(f"  Other related network events: {len(other_related_events)} (e.g. initial requests not yet failed/succeeded, other statuses)")


if __name__ == "__main__":
    asyncio.run(main())

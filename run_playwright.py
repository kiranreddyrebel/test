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
        network_requests_raw = [] # Store all network related events

        # Event handlers for the main page
        page.on("dialog", lambda dialog: (alerts.append(f"Dialog: {dialog.message} (URL: {dialog.page.url})"), asyncio.ensure_future(dialog.accept())))
        page.on("console", lambda msg: console_messages.append({"source": "main_page_or_outer_iframe", "type": msg.type, "text": msg.text, "location": msg.location}))

        def handle_request(request):
            network_requests_raw.append({
                "event_type": "request",
                "url": request.url,
                "method": request.method,
                "headers": request.headers,
                "frame_url": request.frame.url
            })
        page.on("request", handle_request)

        def handle_request_failed(request):
            # According to Playwright docs for Python:
            # request.failure is a property that returns a RequestFailure object.
            # RequestFailure object has .errorText attribute.
            # The previous error ('str' object has no attribute 'errorText') on failure_info.errorText
            # suggests failure_info was a string. This happens if request.failure itself is the error string.
            # Let's assume request.failure is the string of the error, or None.
            failure_text_val = request.failure
            network_requests_raw.append({
                "event_type": "requestfailed",
                "url": request.url,
                "method": request.method,
                "failure_text": failure_text_val if failure_text_val else "N/A",
                "frame_url": request.frame.url
            })
        page.on("requestfailed", handle_request_failed)

        def handle_response(response):
            network_requests_raw.append({
                "event_type": "response",
                "url": response.url,
                "status": response.status,
                "headers": response.headers,
                "frame_url": response.frame.url
            })
        page.on("response", handle_response)


        file_path = "file://" + os.path.join(os.getcwd(), "render_security_test_iframe.html")

        await page.goto(file_path, wait_until="networkidle")

        main_html_content = await page.content()

        outer_iframe_html = "Outer iframe not found or content not accessible."
        inner_iframe1_html = "Inner iframe 1 (file) not found or content not accessible."
        inner_iframe2_html = "Inner iframe 2 (metadata) not found or content not accessible."

        if len(page.frames) > 1:
            outer_iframe = page.frames[1] # srcdoc iframe
            try:
                outer_iframe_html = await outer_iframe.content()
            except Exception as e:
                outer_iframe_html = f"Could not access outer iframe content: {str(e)}"
                console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get outer_iframe content: {e}", "location": {}})


            if len(outer_iframe.child_frames) > 0:
                inner_iframe1 = outer_iframe.child_frames[0] # file:///etc/passwd
                try:
                    inner_iframe1_html = await inner_iframe1.content()
                except Exception as e:
                    inner_iframe1_html = f"Could not access inner iframe 1 (file) content: {str(e)}"
                    console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get inner_iframe1 content: {e}", "location": {}})

            if len(outer_iframe.child_frames) > 1:
                inner_iframe2 = outer_iframe.child_frames[1] # http://169.254.169.254/...
                try:
                    inner_iframe2_html = await inner_iframe2.content()
                except Exception as e:
                    inner_iframe2_html = f"Could not access inner iframe 2 (metadata) content: {str(e)}"
                    console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get inner_iframe2 content: {e}", "location": {}})
        else:
            outer_iframe_html = "Outer iframe not found."

        await browser.close()

        with open("sec_test_alerts.txt", "w") as f:
            if alerts: f.writelines([a + "\n" for a in alerts])
            else: f.write("No alerts captured.\n")

        with open("sec_test_console.json", "w") as f:
            json.dump(console_messages, f, indent=2)

        with open("sec_test_dom.html", "w") as f:
            f.write("--- MAIN PAGE HTML ---\n")
            f.write(main_html_content)
            f.write("\n\n--- OUTER IFRAME (SRCDOC) DOCUMENT HTML ---\n")
            f.write(outer_iframe_html)
            f.write("\n\n--- INNER IFRAME 1 (file:///etc/passwd) DOCUMENT HTML ---\n")
            f.write(inner_iframe1_html)
            f.write("\n\n--- INNER IFRAME 2 (metadata) DOCUMENT HTML ---\n")
            f.write(inner_iframe2_html)

        with open("sec_test_network.json", "w") as f:
            json.dump(network_requests_raw, f, indent=2)

        file_requests_summary = [req for req in network_requests_raw if req['event_type'] == 'requestfailed' and "file:///etc/passwd" in req["url"]]
        metadata_requests_summary = [req for req in network_requests_raw if "169.254.169.254" in req["url"]]

        print("--- ALERTS (saved to sec_test_alerts.txt) ---")
        if not alerts: print("No alerts captured.")
        else:
            for alert in alerts: print(alert)

        print(f"\n--- CONSOLE MESSAGES (saved to sec_test_console.json) ---")
        print(f"{len(console_messages)} console messages saved.")

        print(f"\n--- HTML CONTENT (saved to sec_test_dom.html) ---")
        print("HTML content (main, outer iframe, two inner iframes) saved.")

        print(f"\n--- FILE:///etc/passwd ATTEMPTS (from network log) ---")
        if file_requests_summary:
            print(f"Found {len(file_requests_summary)} failed request entries for file:///etc/passwd.")
            for req in file_requests_summary:
                 print(f"- URL: {req['url']}, Failed: {req['failure_text']}, Frame URL: {req['frame_url']}")
        else:
            print("No failed network log entries for file:///etc/passwd (check console for other errors like 'Not allowed to load local resource').")

        print(f"\n--- METADATA (169.254.169.254) ATTEMPTS (from network log) ---")
        successful_metadata_responses = [req for req in metadata_requests_summary if req['event_type'] == 'response' and req['status'] == 200]
        failed_metadata_requests = [req for req in metadata_requests_summary if req['event_type'] == 'requestfailed']

        if successful_metadata_responses:
            print(f"Found {len(successful_metadata_responses)} successful responses from metadata URL:")
            for req in successful_metadata_responses:
                 print(f"- URL: {req['url']}, Status: {req.get('status', 'N/A')}, Frame URL: {req['frame_url']}")
        elif failed_metadata_requests:
            print(f"Found {len(failed_metadata_requests)} failed requests for metadata URL:")
            for req in failed_metadata_requests:
                 print(f"- URL: {req['url']}, Failed: {req['failure_text']}, Frame URL: {req['frame_url']}")
        else:
            print("No conclusive network log entries (success or fail) for metadata URL. Other network events might exist in the JSON file.")

if __name__ == "__main__":
    asyncio.run(main())

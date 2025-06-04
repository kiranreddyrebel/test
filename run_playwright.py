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
        file_path = "file://" + os.path.join(os.getcwd(), "render_gcp_ssrf_test_iframe.html")

        await page.goto(file_path, wait_until="networkidle")

        main_html_content = await page.content()

        outer_iframe_html = "Outer iframe not found or content not accessible."
        inner_iframe_html = "Inner iframe (GCP SSRF test) not found or content not accessible." # Only one inner iframe in this test

        if len(page.frames) > 1:
            outer_iframe = page.frames[1]
            try:
                outer_iframe_html = await outer_iframe.content()
            except Exception as e:
                outer_iframe_html = f"Could not access outer iframe content: {str(e)}"
                console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get outer_iframe content: {e}", "location": {}})

            if len(outer_iframe.child_frames) > 0: # Should be exactly one child frame
                inner_iframe_obj = outer_iframe.child_frames[0]
                iframe_name = f"Inner iframe (src: {inner_iframe_obj.url})"
                try:
                    inner_iframe_html = await inner_iframe_obj.content()
                except Exception as e:
                    inner_iframe_html = f"Could not access content for {iframe_name}: {str(e)}"
                    console_messages.append({"source": "script_info", "type": "error", "text": f"Failed to get content for {iframe_name}: {e}", "location": {}})
            else:
                 inner_iframe_html = "No inner iframe found inside outer iframe."
        else:
            outer_iframe_html = "Outer iframe not found."

        await browser.close()

        # FILENAMES CHANGED HERE
        with open("gcp_ssrf_alerts.txt", "w") as f:
            if alerts: f.writelines([a + "\n" for a in alerts])
            else: f.write("No alerts captured.\n")

        with open("gcp_ssrf_console.json", "w") as f:
            json.dump(console_messages, f, indent=2)

        with open("gcp_ssrf_dom.html", "w") as f:
            f.write("--- MAIN PAGE HTML ---\n")
            f.write(main_html_content)
            f.write("\n\n--- OUTER IFRAME (SRCDOC) DOCUMENT HTML ---\n")
            f.write(outer_iframe_html)
            f.write(f"\n\n--- INNER IFRAME (GCP SSRF Test) DOCUMENT HTML ---\n") # Adjusted for single inner iframe
            f.write(inner_iframe_html)


        with open("gcp_ssrf_network.json", "w") as f:
            json.dump(network_requests_raw, f, indent=2)

        # Summary printouts
        print("--- ALERTS (saved to gcp_ssrf_alerts.txt) ---")
        if not alerts: print("No alerts captured.")
        else:
            for alert in alerts: print(alert)

        print(f"\n--- CONSOLE MESSAGES (saved to gcp_ssrf_console.json) ---")
        print(f"{len(console_messages)} console messages saved.")

        print(f"\n--- HTML CONTENT (saved to gcp_ssrf_dom.html) ---")
        print(f"HTML content (main, outer iframe, 1 inner iframe) saved.") # Adjusted for single inner iframe

        print(f"\n--- NETWORK ACTIVITY SUMMARY (details in gcp_ssrf_network.json) ---")

        ssrf_custom_domain_requests = [req for req in network_requests_raw if "ssrf.localdomain.pw/custom-200/" in req["url"]]
        gcp_metadata_requests = [req for req in network_requests_raw if "metadata.google.internal" in req["url"]]

        print(f"Requests to ssrf.localdomain.pw/custom-200/: {len([r for r in ssrf_custom_domain_requests if r['event_type']=='request'])}")
        for req in ssrf_custom_domain_requests:
            if req['event_type'] == 'response':
                print(f"- Response from {req['url']}: Status {req['status']}")
            elif req['event_type'] == 'requestfailed':
                print(f"- Failed request to {req['url']}: {req['failure_text']}")

        print(f"Requests to metadata.google.internal (potentially via SSRF custom test): {len([r for r in gcp_metadata_requests if r['event_type']=='request'])}")
        successful_metadata_responses = [req for req in gcp_metadata_requests if req['event_type'] == 'response' and req['status'] == 200]
        failed_metadata_requests = [req for req in gcp_metadata_requests if req['event_type'] == 'requestfailed']

        if successful_metadata_responses:
            print(f"Found {len(successful_metadata_responses)} successful responses from metadata.google.internal:")
            for req in successful_metadata_responses:
                 print(f"- URL: {req['url']}, Status: {req.get('status', 'N/A')}, Frame URL: {req['frame_url']}")
        elif failed_metadata_requests:
            print(f"Found {len(failed_metadata_requests)} failed requests for metadata.google.internal:")
            for req in failed_metadata_requests:
                 print(f"- URL: {req['url']}, Failed: {req['failure_text']}, Frame URL: {req['frame_url']}")
        else:
            print("No conclusive network log entries (success or fail) for metadata.google.internal.")


if __name__ == "__main__":
    asyncio.run(main())

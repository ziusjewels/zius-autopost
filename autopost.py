#!/usr/bin/env python3
"""Zius Jewels daily autoposter.

Posts the day's carousel to the Facebook Page and Instagram via the Meta Graph API.
Runs from GitHub Actions (see .github/workflows/autopost.yml). Standard library only.

Env:
  META_TOKEN   long-lived user token (or a Page token) with pages_manage_posts + instagram_content_publish
  DRY_RUN      "true" = check token, accounts and photo links, post nothing
  FORCE_DAY    plan day number (1-46) to post instead of today's
  PAGE_ID, IG_USER_ID, GRAPH_VERSION, IMAGE_BASE  optional overrides
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/" + os.environ.get("GRAPH_VERSION", "v23.0")
PAGE_ID = os.environ.get("PAGE_ID") or "1370713049453826"          # Zius Jewels
IG_USER_ID = os.environ.get("IG_USER_ID") or "17841470700303423"   # @ziusjewels
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
IMAGE_BASE = os.environ.get("IMAGE_BASE") or f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
GULF = dt.timezone(dt.timedelta(hours=4))
DRY = os.environ.get("DRY_RUN", "false").lower() == "true"
HERE = os.path.dirname(os.path.abspath(__file__))


class GraphError(Exception):
    pass


def call(method, path, token, **params):
    params["access_token"] = token
    data = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{path}"
    if method == "GET":
        url, data = f"{url}?{data.decode()}", None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method), timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                err = json.loads(body).get("error", {})
            except ValueError:
                err = {"message": body[:300]}
            transient = e.code >= 500 or err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 341)
            if transient and attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise GraphError(f"{method} {path}: {err.get('message')} (code {err.get('code')}, sub {err.get('error_subcode')})")
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise GraphError(f"{method} {path}: network error {e}")


def page_token(user_token):
    """Turn the stored user token into this Page's token (or accept a Page token as-is)."""
    try:
        accts = call("GET", "me/accounts", user_token, fields="id,name,access_token", limit=100)
        for a in accts.get("data", []):
            if a["id"] == PAGE_ID:
                return a["access_token"], a["name"]
    except GraphError as e:
        print(f"  me/accounts not available ({e}); trying token as a Page token")
    me = call("GET", "me", user_token, fields="id,name")
    if me.get("id") == PAGE_ID:
        return user_token, me.get("name")
    raise GraphError(f"Token has no access to Page {PAGE_ID}. Re-create it and select the Zius Jewels Page.")


def url_ok(url):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200, r.headers.get("Content-Type")
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def post_facebook(pt, entry, urls):
    ids = []
    for u in urls:
        ids.append(call("POST", f"{PAGE_ID}/photos", pt, url=u, published="false")["id"])
    params = {"message": entry["caption_facebook"]}
    for i, pid in enumerate(ids):
        params[f"attached_media[{i}]"] = json.dumps({"media_fbid": pid})
    post = call("POST", f"{PAGE_ID}/feed", pt, **params)
    cdn = []
    for pid in ids:  # Facebook's own copies of the photos: a fallback source for Instagram
        try:
            imgs = call("GET", pid, pt, fields="images").get("images", [])
            cdn.append(max(imgs, key=lambda x: x.get("width", 0))["source"] if imgs else None)
        except GraphError:
            cdn.append(None)
    return post["id"], cdn


def wait_ready(pt, container):
    for _ in range(30):
        st = call("GET", container, pt, fields="status_code,status").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise GraphError(f"Instagram could not process media {container}: {st}")
        time.sleep(5)
    raise GraphError(f"Instagram media {container} still processing after 150s")


def post_instagram(pt, entry, urls, fallback_urls):
    def build(sources):
        kids = [call("POST", f"{IG_USER_ID}/media", pt, image_url=u, is_carousel_item="true")["id"] for u in sources]
        for k in kids:
            wait_ready(pt, k)
        return call("POST", f"{IG_USER_ID}/media", pt, media_type="CAROUSEL",
                    children=",".join(kids), caption=entry["caption_instagram"])["id"]
    try:
        container = build(urls)
    except GraphError as e:
        if not fallback_urls or not all(fallback_urls):
            raise
        print(f"  Instagram rejected GitHub photo links ({e}); retrying with Facebook's copies")
        container = build(fallback_urls)
    wait_ready(pt, container)
    media_id = call("POST", f"{IG_USER_ID}/media_publish", pt, creation_id=container)["id"]
    link = call("GET", media_id, pt, fields="permalink").get("permalink", "")
    return media_id, link


def main():
    token = os.environ.get("META_TOKEN", "").strip()
    if not token:
        sys.exit("META_TOKEN secret is missing. Add it in GitHub: Settings > Secrets and variables > Actions.")
    sched = json.load(open(os.path.join(HERE, "schedule.json"), encoding="utf-8"))
    state_path = os.path.join(HERE, "posted.json")
    state = json.load(open(state_path))

    force = os.environ.get("FORCE_DAY", "").strip()
    if force:
        entry = next((e for e in sched if e["day"] == int(force)), None)
    else:
        today = dt.datetime.now(GULF).date().isoformat()
        entry = next((e for e in sched if e["date"] == today), None)
    if not entry:
        print("Nothing scheduled for today (Gulf time). Plan runs", sched[0]["date"], "to", sched[-1]["date"])
        return

    print(f"Day {entry['day']} · {entry['date']} · {entry['sku']} {entry['design']}  (dry run: {DRY})")
    pt, page_name = page_token(token)
    ig = call("GET", IG_USER_ID, pt, fields="username")
    print(f"  Facebook Page: {page_name} · Instagram: @{ig.get('username')}")
    urls = [IMAGE_BASE + urllib.parse.quote(p) for p in entry["images"]]

    if DRY:
        print(f"::notice::Connected to Facebook Page '{page_name}' and Instagram @{ig.get('username')}")
        try:
            info = call("GET", "debug_token", token, input_token=token).get("data", {})
            exp = info.get("expires_at", 0)
            if exp:
                days = (exp - time.time()) / 86400
                when = dt.datetime.fromtimestamp(exp, GULF).strftime("%d %b %Y")
                print(f"::notice::Meta token expires {when} ({days:.0f} days left)")
                if days < 3:
                    sys.exit(f"::error::Meta token expires in {days * 24:.1f} hours. Use 'Extend Access Token' and save the long token as META_TOKEN.")
            else:
                print("::notice::Meta token never expires")
        except GraphError as e:
            print("  could not read token expiry:", e)
        bad = 0
        for u in urls:
            ok, info = url_ok(u)
            bad += not ok
            print(f"  photo {'OK ' if ok else 'BAD'} {info}  {u}")
        print(f"::notice::Photo links: {len(urls) - bad} of {len(urls)} reachable")
        try:
            lim = call("GET", f"{IG_USER_ID}/content_publishing_limit", pt, fields="quota_usage,config")
            print("  Instagram publishing quota:", json.dumps(lim.get("data", lim)))
        except GraphError as e:
            print("  could not read Instagram quota:", e)
        print("Dry run finished: token, accounts and photos checked. Nothing was posted.")
        return

    done = state.setdefault(entry["sku"], {})
    failures = []
    fb_cdn = None
    if "facebook" not in done:
        try:
            post_id, fb_cdn = post_facebook(pt, entry, urls)
            done["facebook"] = {"id": post_id, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
            print(f"::notice::Facebook posted: https://facebook.com/{post_id}")
        except GraphError as e:
            failures.append(f"Facebook: {e}")
        json.dump(state, open(state_path, "w"), indent=1)
    else:
        print("  Facebook already posted earlier, skipping")
    if "instagram" not in done:
        try:
            mid, link = post_instagram(pt, entry, urls, fb_cdn)
            done["instagram"] = {"id": mid, "link": link, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
            print(f"::notice::Instagram posted: {link}")
        except GraphError as e:
            failures.append(f"Instagram: {e}")
        json.dump(state, open(state_path, "w"), indent=1)
    else:
        print("  Instagram already posted earlier, skipping")
    if failures:
        sys.exit("::error::" + " / ".join(failures))


if __name__ == "__main__":
    try:
        main()
    except GraphError as e:
        sys.exit(f"FAILED: {e}")

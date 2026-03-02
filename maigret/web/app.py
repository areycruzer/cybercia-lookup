"""
CyberCIA Lookup — Web Application & REST API
"""
import requests as _requests  # used by /proxy endpoint
from flask import (
    Flask,
    render_template,
    request,
    Response,
    jsonify,
    Blueprint,
)
import logging
import os
import asyncio
import sys as _sys
from datetime import datetime
from threading import Thread
import maigret
import maigret.settings
from maigret.sites import MaigretDatabase
from maigret.report import generate_report_context
from maigret.__version__ import __version__ as _ENGINE_VERSION
from maigret.notify import QueryNotify
from maigret.result import MaigretCheckStatus

# ─────────────────────────────────────────────
# App Setup
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())

# In-memory job store
background_jobs = {}
job_results = {}
job_progress = {}

app.config["DB_FILE"]      = os.path.join('maigret', 'resources', 'data.json')
app.config["COOKIES_FILE"] = "cookies.txt"
app.config["REPORTS_DIR"]  = os.path.abspath('/tmp/cybercia_reports')

APP_NAME    = "CyberCIA Lookup"
APP_VERSION = "1.0.0"


# ─────────────────────────────────────────────
# Progress-aware notifier
# ─────────────────────────────────────────────
class ProgressNotify(QueryNotify):
    def __init__(self, job_id, total_sites=0):
        super().__init__()
        self.job_id = job_id
        job_progress[job_id] = {'checked': 0, 'total': total_sites, 'found': [], 'username': '', 'done': False}

    def start(self, message=None, id_type="username"):
        if self.job_id in job_progress:
            job_progress[self.job_id]['username'] = str(message or '')

    def update(self, result, is_similar=False):
        self.result = result
        if self.job_id not in job_progress:
            return
        p = job_progress[self.job_id]
        p['checked'] = p.get('checked', 0) + 1
        if result and result.status == MaigretCheckStatus.CLAIMED:
            p['found'].append({'site': result.site_name, 'url': result.site_url_user or ''})

    def finish(self, message=None):
        if self.job_id in job_progress:
            job_progress[self.job_id]['done'] = True

    def warning(self, *a, **k): pass
    def success(self, *a, **k): pass
    def info(self, *a, **k): pass


# ─────────────────────────────────────────────
# CORS
# ─────────────────────────────────────────────
@app.after_request
def add_cors(response):
    if request.path.startswith('/api/'):
        response.headers['Access-Control-Allow-Origin']  = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

@app.route('/api/', defaults={'path': ''}, methods=['OPTIONS'])
@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_options(path):
    r = Response('')
    r.headers['Access-Control-Allow-Origin']  = '*'
    r.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
    r.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return r, 200


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def api_error(msg, status=400):
    return jsonify({'error': msg}), status


def _load_db():
    return MaigretDatabase().load_from_path(app.config["DB_FILE"])


async def _run_search(username, options, job_id=None):
    logger = logging.getLogger('cybercia')
    logger.setLevel(logging.WARNING)
    db = _load_db()

    top = int(options.get('top_sites') or 500)
    if options.get('all_sites'):
        top = 999999999

    sites = db.ranked_sites_dict(
        top=top, tags=options.get('tags', []),
        names=options.get('site_list', []),
        disabled=False, id_type='username',
    )

    notify = ProgressNotify(job_id, total_sites=len(sites)) if job_id else None
    if notify:
        job_progress[job_id]['total'] = len(sites)

    return await maigret.search(
        username=username, site_dict=sites,
        timeout=int(options.get('timeout', 30)),
        logger=logger, query_notify=notify, id_type='username',
        cookies=app.config["COOKIES_FILE"] if options.get('use_cookies') else None,
        is_parsing_enabled=not options.get('disable_extracting', False),
        recursive_search_enabled=not options.get('disable_recursive_search', False),
        check_domains=options.get('with_domains', False),
        proxy=options.get('proxy') or None,
        tor_proxy=options.get('tor_proxy') or None,
        i2p_proxy=options.get('i2p_proxy') or None,
        no_progressbar=True,
    )


def _process_job(usernames, options, jid):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        all_results = []
        for uname in usernames:
            try:
                r = loop.run_until_complete(_run_search(uname.strip(), options, job_id=jid))
                all_results.append((uname.strip(), 'username', r))
            except Exception as e:
                logging.error(f"Search error for {uname}: {e}")

        os.makedirs(app.config["REPORTS_DIR"], exist_ok=True)
        session_dir = os.path.join(app.config["REPORTS_DIR"], f"search_{jid}")
        os.makedirs(session_dir, exist_ok=True)

        individual = []
        for username, id_type, results in all_results:
            base = os.path.join(session_dir, f"report_{username}")
            ctx  = generate_report_context(all_results)
            maigret.report.save_csv_report(f"{base}.csv", username, results)
            maigret.report.save_json_report(f"{base}.json", username, results, report_type='ndjson')
            claimed = []
            for site_name, sd in results.items():
                if sd.get('status') and sd['status'].status == MaigretCheckStatus.CLAIMED:
                    claimed.append({'site_name': site_name, 'url': sd.get('url_user',''), 'tags': (sd['status'].tags or [])})
            individual.append({
                'username': username,
                'csv_file': os.path.join(f"search_{jid}", f"report_{username}.csv"),
                'json_file': os.path.join(f"search_{jid}", f"report_{username}.json"),
                'claimed_profiles': claimed,
            })

        job_results[jid] = {'status': 'completed', 'session_folder': f"search_{jid}", 'usernames': usernames, 'individual_reports': individual}
    except Exception as e:
        logging.error(f"Job {jid} failed: {e}")
        job_results[jid] = {'status': 'failed', 'error': str(e)}
    finally:
        background_jobs[jid]['completed'] = True
        if jid in job_progress:
            job_progress[jid]['done'] = True


def _start_job(usernames, options):
    jid = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    background_jobs[jid] = {
        'completed': False,
        'thread': Thread(target=_process_job, args=(usernames, options, jid)),
    }
    background_jobs[jid]['thread'].start()
    return jid


# ─────────────────────────────────────────────
# Web UI (single-page app)
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/docs')
def api_docs_redirect():
    return jsonify({'message': 'API docs available via /api/v1/health'}), 200


# ─────────────────────────────────────────────
# REST API — /api/v1/
# ─────────────────────────────────────────────
api = Blueprint('api_v1', __name__, url_prefix='/api/v1')


@api.route('/health', methods=['GET'])
def api_health():
    return jsonify({
        'status': 'ok',
        'name': APP_NAME,
        'version': APP_VERSION,
    })


@api.route('/db', methods=['GET'])
def api_db():
    """Sites database with URL patterns for client-side checking."""
    try:
        limit = int(request.args.get('limit', 500))
        db    = _load_db()
        out   = []
        for site in db.sites:
            if site.disabled or site.type != 'username':
                continue
            out.append({
                'name':         site.name,
                'url':          site.url,
                'url_main':     site.url_main or '',
                'check_type':   site.check_type or 'status_code',
                'absence_strs': site.absence_strs or [],
                'presense_strs':site.presense_strs or [],
                'tags':         list(site.tags) if site.tags else [],
                'alexa_rank':   site.alexa_rank if site.alexa_rank != _sys.maxsize else None,
            })
            if len(out) >= limit:
                break
        return jsonify({'total': len(out), 'sites': out})
    except Exception as e:
        return api_error(f'DB load failed: {str(e)}', 500)


@api.route('/proxy', methods=['GET'])
def api_proxy():
    """HTTP proxy — lets browser JS check sites without CORS restrictions."""
    target = request.args.get('url', '').strip()
    if not target or not (target.startswith('http://') or target.startswith('https://')):
        return api_error('Valid `url` parameter required')
    try:
        resp = _requests.get(
            target, timeout=12, allow_redirects=True,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; CyberCIA/1.0)'},
        )
        return jsonify({'status': resp.status_code, 'final_url': resp.url, 'content': resp.text[:8000]})
    except _requests.exceptions.Timeout:
        return jsonify({'status': 0, 'final_url': target, 'content': '', 'error': 'timeout'})
    except Exception as e:
        return jsonify({'status': 0, 'final_url': target, 'content': '', 'error': str(e)})


@api.route('/sites', methods=['GET'])
def api_sites():
    try:
        limit      = int(request.args.get('limit', 50))
        tag_filter = request.args.get('tag', None)
        db         = _load_db()
        all_tags   = set()
        sites_out  = []
        for site in db.sites:
            tags = list(site.tags) if site.tags else []
            all_tags.update(tags)
            if tag_filter and tag_filter not in tags:
                continue
            sites_out.append({
                'name':      site.name,
                'url_main':  site.url_main,
                'tags':      tags,
                'alexa_rank':getattr(site, 'alexa_rank', None),
                'disabled':  getattr(site, 'disabled', False),
            })
        return jsonify({'total': len(db.sites), 'returned': len(sites_out[:limit]), 'tags': sorted(all_tags), 'sites': sites_out[:limit]})
    except Exception as e:
        return api_error(f'Failed to load sites: {str(e)}', 500)


@api.route('/search', methods=['POST'])
def api_search():
    data = request.get_json(silent=True)
    if not data:
        return api_error('Request body must be JSON')
    usernames = [str(u).strip() for u in data.get('usernames', []) if str(u).strip()]
    if not usernames:
        return api_error('`usernames` must be a non-empty list')
    options = data.get('options', {})
    options.setdefault('top_sites', 500)
    options.setdefault('timeout', 30)
    jid = _start_job(usernames, options)
    return jsonify({
        'job_id': jid, 'status': 'running', 'usernames': usernames,
        'status_url': f'/api/v1/status/{jid}',
        'results_url': f'/api/v1/results/{jid}',
        'progress_url': f'/api/v1/progress/{jid}',
    }), 202


@api.route('/progress/<jid>', methods=['GET'])
def api_progress(jid):
    if jid not in background_jobs:
        return api_error(f'Job `{jid}` not found', 404)
    p   = job_progress.get(jid, {})
    chk = p.get('checked', 0)
    tot = p.get('total', 0)
    return jsonify({
        'job_id': jid, 'checked': chk, 'total': tot,
        'percent': round((chk / tot) * 100) if tot else 0,
        'found': p.get('found', []), 'found_count': len(p.get('found', [])),
        'username': p.get('username', ''), 'done': p.get('done', False),
    })


@api.route('/status/<jid>', methods=['GET'])
def api_status(jid):
    if jid not in background_jobs:
        return api_error(f'Job `{jid}` not found', 404)
    if not background_jobs[jid]['completed']:
        p = job_progress.get(jid, {})
        return jsonify({'job_id': jid, 'status': 'running', 'progress': {'checked': p.get('checked', 0), 'total': p.get('total', 0), 'found_count': len(p.get('found', []))}})
    result = job_results.get(jid)
    if not result:
        return api_error('Job completed but results missing', 500)
    if result['status'] == 'failed':
        return jsonify({'job_id': jid, 'status': 'failed', 'error': result.get('error')})
    return jsonify({'job_id': jid, 'status': 'completed', 'usernames': result.get('usernames', []), 'results_url': f'/api/v1/results/{jid}'})


@api.route('/results/<jid>', methods=['GET'])
def api_results(jid):
    if jid not in background_jobs:
        return api_error(f'Job `{jid}` not found', 404)
    if not background_jobs[jid]['completed']:
        return jsonify({'job_id': jid, 'status': 'running', 'message': f'Poll /api/v1/status/{jid}'}), 202
    result = job_results.get(jid)
    if not result:
        return api_error('Results not found', 500)
    if result['status'] == 'failed':
        return jsonify({'job_id': jid, 'status': 'failed', 'error': result.get('error')})
    out = [{'username': r['username'], 'claimed_profiles': r['claimed_profiles'], 'total_found': len(r['claimed_profiles']),
            'downloads': {'csv': f'/reports/{r["csv_file"]}', 'json': f'/reports/{r["json_file"]}'}}
           for r in result.get('individual_reports', [])]
    return jsonify({'job_id': jid, 'status': 'completed', 'usernames': result.get('usernames', []), 'results': out})


@api.route('/jobs/<jid>', methods=['DELETE'])
def api_delete_job(jid):
    if jid not in background_jobs:
        return api_error(f'Job `{jid}` not found', 404)
    if not background_jobs[jid]['completed']:
        return api_error('Cannot delete a running job', 409)
    background_jobs.pop(jid, None)
    job_results.pop(jid, None)
    job_progress.pop(jid, None)
    return jsonify({'message': f'Job {jid} cleared.'})


# ─────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Endpoint not found'}), 404
    return render_template('index.html'), 404

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('index.html'), 500


app.register_blueprint(api)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    host  = os.getenv('FLASK_HOST', '127.0.0.1')
    port  = int(os.getenv('FLASK_PORT', '5000'))
    debug = os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1']
    print(f"\n⬡ {APP_NAME} v{APP_VERSION}")
    print(f"⬡ http://{host}:{port}/\n")
    app.run(host=host, port=port, debug=debug)

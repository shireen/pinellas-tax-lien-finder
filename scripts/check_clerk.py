"""Public-data-only Clerk smoke check. Does not read config, send email, or print records."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taxlien import liens


def main():
    for verb in ('get', 'post'):
        original = getattr(liens, verb)
        def traced(s, url, _original=original, _verb=verb, **kwargs):
            # Paths are fixed in the client; never print query params, bodies or cookies.
            stage = url.split('?', 1)[0].rsplit('/', 1)[-1]
            try:
                response = _original(s, url, **kwargs)
            except Exception as error:
                response = getattr(error, 'response', None)
                status = getattr(response, 'status_code', None)
                print(f'Clerk {_verb} {stage}: {type(error).__name__}, HTTP {status}', flush=True)
                raise
            print(f'Clerk {_verb} {stage}: HTTP {response.status_code}', flush=True)
            return response
        setattr(liens, verb, traced)
    try:
        # This sample already appears in the client's docstring; not the user's address.
        rows = liens.Clerk().search_name('SCHILLINGER LISA')
        if not rows:
            print('Clerk smoke check: expected sample records were not returned.')
            return 1
        print(f'Clerk smoke check: completed ({len(rows)} rows).')
        return 0
    except Exception as error:
        print(f'Clerk smoke check: incomplete ({type(error).__name__}).')
        return 1


if __name__ == '__main__':
    sys.exit(main())

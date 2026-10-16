# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately through GitHub: on this repository, open the **Security** tab and
choose **Report a vulnerability** (GitHub private vulnerability reporting). Include:

- the version or commit you tested;
- a minimal input (trades CSV, rules file, command line) that shows the problem;
- what happens and what you expected.

You should get a first answer within 7 days. Once a fix is published, the report can be
made public, with credit if you want it.

## Scope

In scope: anything that lets an input file (trades CSV, rules TOML) read or write outside
the output and cache directories, inject parameters into download URLs, run code, or
produce report files (HTML, CSV) that execute content when opened.

Already handled, with regression tests in `tests/test_security.py`:

- symbols are validated (`^[A-Z0-9]{2,20}$`, Windows device names refused, optional
  allow-list) before any cache path or URL is built; cache paths are resolved and checked
  to stay inside the cache directory; URL parameters are encoded;
- text cells in every CSV the tool writes are prefixed with an apostrophe when they start
  with `=`, `+`, `-`, `@`, a tab or a carriage return (numbers are left as they are);
- every value in the HTML report is HTML-escaped.

Out of scope: `--allow-pickle` (only unpickle files you created yourself) and the Markdown
report, which is not escaped and should not be served as HTML.

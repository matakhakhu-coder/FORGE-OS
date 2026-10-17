import pathlib, re, sys
pat = re.compile(r"&amp;(nbsp|amp|quot|lt|gt|#\d+);")
for f in sys.argv[1:]:
    p = pathlib.Path(f)
    s = open(p, encoding="utf-8", newline="").read()
    s2, n = pat.subn(r"&\1;", s)
    open(p, "w", encoding="utf-8", newline="").write(s2)
    print(f, "- unwrapped", n, "double-escaped entities")
# Third-party data provenance

SWE-bench-jl redistributes `patch` and `test_patch` slices (short unified diffs and
associated test and issue text) extracted from MIT-licensed Julia packages: the five
seed packages detailed first, plus eight packages added during v0.2 scaling (listed in
the final section). All thirteen carry the standard MIT permission grant.
The harness code itself is Copyright (c) 2026 Yusuke Izawa and is released under
the MIT License (see `LICENSE`).

The per-instance provenance (`repo_url` and `fix_commit`) is embedded in every record
of `data/instances.jsonl`, so the origin of each diff is machine-readable.

---

## DataStructures.jl

**Repository:** https://github.com/JuliaCollections/DataStructures.jl

**Note:** The license file carries the MIT permission text but only a bare copyright
header — no "MIT" label appears in the file. The permission grant is identical to the
standard MIT License.

**Verbatim upstream copyright and permission notice** (from `License.md`):

```
Copyright (c) 2013 Dahua Lin

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## OrderedCollections.jl

**Repository:** https://github.com/JuliaCollections/OrderedCollections.jl

**Note:** Like DataStructures.jl, the license file carries the MIT permission text but
only a bare copyright header — no "MIT" label appears in the file. The permission grant
is identical to the standard MIT License.

**Verbatim upstream copyright and permission notice** (from `License.md`):

```
Copyright (c) 2013 Dahua Lin

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## Combinatorics.jl

**Repository:** https://github.com/JuliaMath/Combinatorics.jl

**License type:** MIT

**Verbatim upstream copyright and permission notice** (from `LICENSE.md`):

```
Combinatorics.jl is licensed under the MIT License:

> Copyright (c) 2013-2015: Alessandro Andrioni, Jiahao Chen and other
> contributors.
>
> Permission is hereby granted, free of charge, to any person obtaining
> a copy of this software and associated documentation files (the
> "Software"), to deal in the Software without restriction, including
> without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to
> permit persons to whom the Software is furnished to do so, subject to
> the following conditions:
>
> The above copyright notice and this permission notice shall be
> included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
> EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
> MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
> LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
> OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
> WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## IterTools.jl

**Repository:** https://github.com/JuliaCollections/IterTools.jl

**License type:** MIT

**Verbatim upstream copyright and permission notice** (from `LICENSE.md`):

```
IterTools.jl is licensed under the MIT License:

> Copyright (c) 2012-2017: Daniel Jones, Stefan Karpinski, Simon Kornblith,
> Kevin Squire, Jeff Bezanson, Tim Holy, Jonathan Malmaud, Eric Davies, and
> other contributors.

> Permission is hereby granted, free of charge, to any person obtaining
> a copy of this software and associated documentation files (the
> "Software"), to deal in the Software without restriction, including
> without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to
> permit persons to whom the Software is furnished to do so, subject to
> the following conditions:
>
> The above copyright notice and this permission notice shall be
> included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
> EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
> MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
> LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
> OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
> WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## JSON.jl

**Repository:** https://github.com/JuliaIO/JSON.jl

**License type:** MIT Expat

**Verbatim upstream copyright and permission notice** (from `LICENSE.md`):

```
The Julia JSON package is licensed under the MIT Expat License:

> Copyright (c) 2002: JSON.org, 2012–2025: Jacob Quinn, Avik Sengupta, Stefan Karpinski,
> David de Laat, Dirk Gadsen, Milo Yip and other contributors
> – https://github.com/JuliaLang/JSON.jl/contributors
>
> Permission is hereby granted, free of charge, to any person obtaining
> a copy of this software and associated documentation files (the
> "Software"), to deal in the Software without restriction, including
> without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to
> permit persons to whom the Software is furnished to do so, subject to
> the following conditions:
>
> The above copyright notice and this permission notice shall be
> included in all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
> EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
> MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
> LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
> OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
> WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

All five seed packages are MIT or MIT-Expat licensed. DataStructures.jl and
OrderedCollections.jl carry the MIT permission text but only a bare
`Copyright (c) 2013 Dahua Lin` header with no "MIT" label; the permission grant is
substantively identical to the standard MIT License. The per-instance provenance
(`repo_url` and `fix_commit`) is embedded in every record of `data/instances.jsonl`.

---

## v0.2 scaling additions

The following eight packages were added during scaling. Each was verified to carry the
standard MIT permission grant (some report `NOASSERTION` to GitHub's license detector
only because the LICENSE file lacks a standard header/filename; the permission text is
the verbatim MIT grant). Per-instance provenance (`repo_url`, `fix_commit`) is embedded
in every record of `data/instances.jsonl`.

| Package | Repository | Upstream copyright |
|---|---|---|
| Distances.jl | https://github.com/JuliaStats/Distances.jl | Copyright (c) 2014: Dahua Lin. |
| QuadGK.jl | https://github.com/JuliaMath/QuadGK.jl | Copyright (c) 2016 Steven G. Johnson and other contributors. |
| Primes.jl | https://github.com/JuliaMath/Primes.jl | Copyright (c) 2016: Stefan Karpinski, Jeff Bezanson, Aitor Ortiz de Latierro Olivella, Pablo Zubieta, Alex Arslan, Simon Byrne and other contributors. |
| SortingAlgorithms.jl | https://github.com/JuliaCollections/SortingAlgorithms.jl | Copyright (c) 2013-2014: Kevin Squire, Stefan Karpinski, Jeff Bezanson. |
| HypergeometricFunctions.jl | https://github.com/JuliaMath/HypergeometricFunctions.jl | Copyright (c) 2018-2023 Richard Mikael Slevinsky and other contributors. |
| InlineStrings.jl | https://github.com/JuliaStrings/InlineStrings.jl | Copyright (c) 2021: Jacob Quinn and JuliaData maintainers. |
| SplitApplyCombine.jl | https://github.com/JuliaData/SplitApplyCombine.jl | Copyright (c) 2017-2019: Andy Ferris. |
| GeoRegions.jl | https://github.com/GeoRegionsEcosystem/GeoRegions.jl | Copyright (c) 2020 natgeo-wong. |

Each package's full license is the standard MIT permission grant, included verbatim
here on behalf of all eight (only the copyright line above differs per package):

```
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

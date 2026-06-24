# Pinned to the v8 crate version in the fork's backend/Cargo.lock (137.3.0).
# Bump this whenever the deno_core / v8 crate moves; the shas are the prebuilt
# static archives denoland publishes per rusty_v8 release. The two zeroed shas
# are placeholders: a build reports the real base32 hashes to fill in.
{ fetchLibrustyV8 }:

fetchLibrustyV8 {
  version = "137.3.0";
  shas = {
    # NOTE; Follows supported platforms of package (see meta.platforms attribute)!
    x86_64-linux = "0rv5nl4gcbvdpk3mdwbqvw180nfx2wk173q1cqrvv2h1agg1ys52";
    aarch64-linux = "14kci8zl7i5cjrbf2jyky5i9gpa4bsjw8khfk4xc8yf1875x0s73";
  };
}

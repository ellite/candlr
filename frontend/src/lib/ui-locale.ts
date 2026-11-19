// Bridges the account's UI language (users.language) into Paraglide's locale
// resolution.
//
// Paraglide resolves a locale from the incoming Request alone, but the account
// preference we want to win over the cookie is fetched by the middleware. A
// custom server strategy is the supported way to feed it in: it runs before the
// built-in ones, so "account preference, else cookie, else English" falls out of
// the ordering in astro.config.mjs.
//
// The preference is keyed on the Request object rather than kept in a module
// variable: requests overlap, and a shared variable would hand one user's
// language to another. The map is weak, so an entry disappears with its request.
import { defineCustomServerStrategy, type Locale } from "../paraglide/runtime.js";

export const ACCOUNT_STRATEGY = "custom-account";

const accountLocales = new WeakMap<Request, Locale>();

/** Called by the middleware once the signed-in user's language is known. */
export function setAccountLocale(request: Request, locale: Locale): void {
  accountLocales.set(request, locale);
}

defineCustomServerStrategy(ACCOUNT_STRATEGY, {
  // Undefined (anonymous visitor, or nothing saved) falls through to the cookie.
  getLocale: (request?: Request) => (request ? accountLocales.get(request) : undefined),
});

# Frontend branding — Build 5.3.2

The supplied Stamford American International School wordmark is used unchanged from the uploaded transparent PNG.

## Asset inventory

- `ui/assets/stamford-school-logo.png` — original 319×80 horizontal wordmark supplied for this change.
- `ui/assets/stamford-school-crest.png` — transparent crest crop derived from the supplied logo and used for the 64×64 browser favicon.

## Locations updated

- Persistent application sidebar: horizontal logo on a white backing so its navy lettering remains legible on the dark sidebar.
- Self-service kiosk welcome/reset screen. Transaction result icons remain icons rather than being replaced by the brand mark.
- Visitor sticker preview and print output.
- Windows printer test label.
- Group visit QR print page.
- Browser favicon and browser theme colour.

The image paths are relative to the `ui/` static root and are served by the existing loopback-only static service. No external image host or network request is introduced. The app title and school-name text remain for usability/accessibility; branding art is not used as the sole accessible label.

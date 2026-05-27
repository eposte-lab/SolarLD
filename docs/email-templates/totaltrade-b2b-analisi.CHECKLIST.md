# Pre-deploy checklist — totaltrade-b2b-analisi.html

Test su **Litmus** o **Email on Acid** prima dell'invio reale.

## Rendering cross-client
- [ ] **Outlook 2016/2019/365 (Windows)** — CTA bulletproof (VML roundrect) renderizzata verde con angoli arrotondati; nessun bordo blu; tabelle non sfondate; font ≥14px leggibile.
- [ ] **Outlook con immagini bloccate** (default aziendale) — l'email resta comprensibile e cliccabile: headline, card €0, metriche, CTA e bullet visibili (sono HTML); alt text mostrati su logo/satellitare/avatar.
- [ ] **Gmail web + Gmail app (iOS/Android)** — gradiente/colori ok; CTA tappabile; nessun clipping ("[Messaggio troncato]" → HTML <102KB).
- [ ] **Apple Mail (macOS + iOS)** — bordi arrotondati, ombre, spaziature corrette.
- [ ] **Libero / Aruba / Yahoo** — layout a tabella integro, nessun overflow.

## Mobile (≥ priorità)
- [ ] **iPhone SE (375px)** — above the fold: nome azienda + kWh + immagine satellitare + card €0 visibili **senza scroll**.
- [ ] Card EPC: claim a sinistra e badge €0 **collassano in stack verticale**.
- [ ] Due metriche **collassano in stack** (una sopra l'altra).
- [ ] CTA: altezza ≥44px, full-tap, centrata.
- [ ] Firma: foto + testo leggibili; telefono `tel:` apre il dialer.

## Dark mode
- [ ] iOS Mail / Apple Mail dark — sfondo pagina scuro, testo headline chiaro, **verde CTA invariato e leggibile**, card navy EPC invariata.
- [ ] Nessun testo nero su sfondo scuro (contrasto rotto).

## Contenuto / personalizzazione
- [ ] Tutti i token `{{...}}` popolati (nessun `{{` residuo nel render).
- [ ] Preheader corretto nell'anteprima inbox (città + kWh + risparmio).
- [ ] Numeri coerenti tra headline, metriche e overlay dell'immagine.
- [ ] Link `landing_url`, `unsubscribe_url`, `privacy_url`, `tel:`, `mailto:` funzionanti.

## Deliverability
- [ ] **Mail-tester.com ≥ 9/10** (SPF/DKIM/DMARC pass, contenuto pulito).
- [ ] Nessun trigger spam: assenza di "gratis", caps-lock, eccesso di "!".
- [ ] Rapporto testo/immagine ≥ 60/40 (1 sola immagine di contenuto).
- [ ] HTML totale <100KB; immagine satellitare <200KB.
- [ ] `landing_url` e i link sullo **stesso dominio mittente** (o riscritti dal click-tracking) per evitare il flag "URL mismatch".

## Compliance
- [ ] Footer con P.IVA + sede legale + base giuridica GDPR.
- [ ] Link "Disiscriviti" funzionante + header `List-Unsubscribe` impostato lato invio.

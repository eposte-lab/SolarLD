-- 0127_total_trade_remove_plenitude.sql
--
-- Rimuove ogni riferimento a ENI Plenitude dalla sezione "Chi siamo"
-- del tenant Total Trade (richiesta cliente: nessun riferimento a
-- Plenitude in nessun punto del prodotto).
--
-- about_md riscritto senza la menzione del partner; about_certifications
-- svuotato (conteneva "Technical partner ENI Plenitude").

UPDATE tenants
SET about_md = 'Total Trade realizza impianti fotovoltaici per le '
               'aziende con il modello EPC: l''investimento è '
               'interamente a nostro carico, l''azienda cliente non '
               'spende nulla e risparmia da subito sulla bolletta. A '
               'fine contratto l''impianto diventa di proprietà '
               'dell''azienda.',
    about_certifications = ARRAY[]::text[],
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';

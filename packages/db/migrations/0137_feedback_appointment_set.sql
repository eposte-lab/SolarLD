-- 0137_feedback_appointment_set.sql
--
-- Aggiunge il valore 'appointment_set' all'enum installer_feedback:
-- l'operatore può segnare dal dashboard ("Esito → Appuntamento
-- fissato") di aver preso un appuntamento con il cliente. Lo step
-- "Appuntamento" della timeline lead si accende di conseguenza.

ALTER TYPE installer_feedback ADD VALUE IF NOT EXISTS 'appointment_set';

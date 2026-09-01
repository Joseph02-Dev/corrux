/**
 * CORRUX — Ouverture des modals de confirmation destructive (UI-204).
 *
 * Script unique, générique, chargé une seule fois (ui/templates/ui/shell/
 * base.html) — jamais dupliqué par instance de modal. Aucune logique
 * métier : se contente d'appeler l'API native <dialog>.showModal().
 *
 * Le piège de focus, le focus initial, la fermeture par Échap, le
 * retour de focus au déclencheur et le fond assombri (::backdrop) sont
 * entièrement gérés par le navigateur via <dialog> — rien de tout cela
 * n'est réimplémenté ici. La fermeture par le bouton "Annuler" utilise
 * <form method="dialog">, également native, sans code JavaScript dédié.
 */
document.addEventListener("click", function (event) {
  var trigger = event.target.closest("[data-modal-target]");
  if (!trigger) {
    return;
  }
  var modal = document.getElementById(trigger.dataset.modalTarget);
  if (modal && typeof modal.showModal === "function") {
    modal.showModal();
  }
});

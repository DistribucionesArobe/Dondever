#!/bin/bash
# Doble clic a este archivo para subir los cambios de la vista de semana.
# Solo sube server.py y templates/canal.html. Lo demás lo deja como está.

cd "$(dirname "$0")" || exit 1

echo "Carpeta: $(pwd)"
echo

if [ -f .git/index.lock ]; then
  echo "Hay un .git/index.lock atorado. Bórralo y vuelve a correr esto:"
  echo "  rm '$(pwd)/.git/index.lock'"
  echo
  read -n 1 -s -r -p "Enter para cerrar..."
  exit 1
fi

echo "1/4 · Trayendo lo que haya en GitHub..."
git pull --rebase || { echo; echo "Falló el pull. Para arriba está el motivo."; read -n 1 -s -r -p "Enter para cerrar..."; exit 1; }

echo
echo "2/4 · Preparando los archivos..."
git add server.py templates/canal.html || exit 1

echo
echo "3/4 · Haciendo el commit..."
if git diff --cached --quiet; then
  echo "No había nada nuevo que guardar — probablemente ya lo subiste."
else
  git commit -m "Vista de 7 dias en paginas de canal + indice compartido de la semana" || exit 1
fi

echo
echo "4/4 · Subiendo..."
git push || { echo; echo "Falló el push. Para arriba está el motivo."; read -n 1 -s -r -p "Enter para cerrar..."; exit 1; }

echo
echo "Listo. Render debería empezar a desplegar en un minuto."
echo
echo "Quedan sin subir (a propósito, revísalos tú):"
git status --short
echo
read -n 1 -s -r -p "Enter para cerrar..."

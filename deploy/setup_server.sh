#!/bin/bash
# Первичная настройка чистого VPS (Ubuntu 22.04/24.04). Запускать один раз от root:
#   bash setup_server.sh
# Что делает: часовой пояс МСК, обновления безопасности, файрвол (только SSH), пользователь gradus
# с вашим SSH-ключом, пакеты (git, python, node), Claude Code, ключ доступа к GitHub (deploy key).
# Секретов не содержит и не спрашивает.
set -euo pipefail

USER_NAME=gradus

[ "$(id -u)" = 0 ] || { echo "Запускать от root"; exit 1; }

echo "== Часовой пояс и пакеты"
timedatectl set-timezone Europe/Moscow    # скрипты пишут время и проверяют рабочие часы по местному времени
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq git python3 python3-venv python3-pip nodejs curl rsync ufw fail2ban unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Пользователь $USER_NAME"
if ! id "$USER_NAME" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$USER_NAME"
fi
# доступ по тому же ключу, что у root (ключ кладёт провайдер при создании сервера)
install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" /home/$USER_NAME/.ssh
if [ -s /root/.ssh/authorized_keys ]; then
  install -m 600 -o "$USER_NAME" -g "$USER_NAME" /root/.ssh/authorized_keys /home/$USER_NAME/.ssh/authorized_keys
fi
# gradus может только запускать/останавливать службы проекта (status и журнал смотрятся без sudo)
cat > /etc/sudoers.d/90gradusov <<'SUDO'
gradus ALL=(root) NOPASSWD: /usr/bin/systemctl start 90g-*, /usr/bin/systemctl stop 90g-*, /usr/bin/systemctl restart 90g-*
SUDO
chmod 440 /etc/sudoers.d/90gradusov
visudo -cf /etc/sudoers.d/90gradusov
usermod -aG systemd-journal "$USER_NAME"

echo "== SSH: только по ключу"
if [ -s /home/$USER_NAME/.ssh/authorized_keys ]; then
  cat > /etc/ssh/sshd_config.d/90gradusov.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
SSHD
  systemctl reload ssh || systemctl reload sshd
else
  echo "ВНИМАНИЕ: SSH-ключа нет, вход по паролю оставлен. Добавьте ключ и запустите скрипт ещё раз."
fi

echo "== Файрвол: открыт только SSH (боты сами ходят наружу, входящие порты не нужны)"
ufw allow OpenSSH
ufw --force enable
systemctl enable --now fail2ban

echo "== Claude Code и ключ для GitHub (от имени $USER_NAME)"
sudo -iu "$USER_NAME" bash <<'AS_USER'
set -euo pipefail
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
grep -q '.local/bin' ~/.profile || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.profile
[ -f ~/.ssh/github_deploy ] || ssh-keygen -q -t ed25519 -N "" -C "90gradusov-vps" -f ~/.ssh/github_deploy
cat > ~/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config
ssh-keyscan -q github.com >> ~/.ssh/known_hosts 2>/dev/null
AS_USER

echo
echo "Готово. Добавьте этот ключ в GitHub: репозиторий → Settings → Deploy keys → Add (Allow write access — включить):"
cat /home/$USER_NAME/.ssh/github_deploy.pub
echo
echo "Дальше (см. docs/15_сервер_VPS.md): от имени $USER_NAME — deploy/install_app.sh, затем от root — deploy/install_services.sh"

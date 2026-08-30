# Guia da versão personalizada

Este guia instala no Omarchy exatamente a versão personalizada deste
repositório, mantendo a instalação oficial disponível para rollback. As
alterações estão na branch `fix/omarchy-session-restore-reliability` do fork
`RafaelGoulartB/omarchy-sesh`.

O procedimento não remove o banco de sessões, a configuração do usuário nem o
modo Manual/Active. Ele troca somente a origem do código do plugin.

## Comportamento do modo Manual

Use esta ordem antes de desligar:

1. Deixe o Zen aberto com todas as janelas que devem voltar.
2. Selecione **Manual** no painel e aguarde a confirmação do snapshot.
3. Feche o Zen normalmente com Quit/Ctrl+Q para que ele grave abas e janelas.
4. Reinicie ou desligue pelo menu do Omarchy.

Em Manual, a ação de energia não salva novamente e não envia atalhos ao Zen.
Assim, o snapshot feito no passo 2 continua contendo o Zen. Em Active, o plugin
continua fazendo o snapshot de desligamento e o encerramento gracioso
automaticamente.

## Instalar a branch local

Confirme primeiro que o código local está na branch correta e é um plugin
válido:

```bash
git -C /home/rafa/Apps/Plugins/omarchy-sesh switch fix/omarchy-session-restore-reliability
omarchy plugin validate /home/rafa/Apps/Plugins/omarchy-sesh
```

Defina os caminhos usados durante a troca:

```bash
sesh_source=/home/rafa/Apps/Plugins/omarchy-sesh
sesh_plugin_dir="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/mrpbennett.sesh"
sesh_backup_root="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy/plugin-backups"
sesh_official_backup="$sesh_backup_root/mrpbennett.sesh-official"
```

Pare se `$sesh_official_backup` já existir: isso significa que há um backup de
uma troca anterior que deve ser inspecionado antes de continuar. Caso contrário,
faça a substituição:

```bash
test ! -e "$sesh_official_backup"
omarchy plugin disable mrpbennett.sesh
mkdir -p "$sesh_backup_root"
mv -- "$sesh_plugin_dir" "$sesh_official_backup"
ln -s -- "$sesh_source" "$sesh_plugin_dir"
omarchy-shell shell rescanPlugins
omarchy plugin enable mrpbennett.sesh --section right
```

Abra o painel **Omarchy Sesh** depois da troca. A verificação do próprio painel
detectará a versão nova e atualizará o CLI, as unidades systemd e as ações do
menu, preservando o banco e o modo selecionado. Não é necessário executar
`install.sh` diretamente.

Verifique a instalação:

```bash
readlink -f "$sesh_plugin_dir"
omarchy plugin list
"$HOME/.local/bin/omarchy-sesh" mode
systemctl --user status omarchy-sesh.service omarchy-sesh-autosave.service
```

O primeiro comando deve mostrar
`/home/rafa/Apps/Plugins/omarchy-sesh`, e a lista deve mostrar
`mrpbennett.sesh` como habilitado.

## Atualizar a branch personalizada

Depois de novos commits enviados ao fork:

```bash
git -C /home/rafa/Apps/Plugins/omarchy-sesh pull --ff-only origin fix/omarchy-session-restore-reliability
```

Abra novamente o painel. Como o diretório instalado é um link para esse
checkout, o Omarchy verá o código atualizado imediatamente e a verificação do
painel sincronizará o CLI quando necessário.

Não use `omarchy plugin update mrpbennett.sesh` enquanto a instalação for esse
link de desenvolvimento. Atualize o checkout local com o comando acima.

## Voltar à versão oficial

Use os mesmos caminhos definidos na instalação e confirme que o destino atual é
um link simbólico antes de removê-lo:

```bash
test -L "$sesh_plugin_dir"
test -d "$sesh_official_backup"
omarchy plugin disable mrpbennett.sesh
unlink "$sesh_plugin_dir"
mv -- "$sesh_official_backup" "$sesh_plugin_dir"
omarchy-shell shell rescanPlugins
omarchy plugin enable mrpbennett.sesh --section right
```

Abra o painel uma vez para que o CLI e os serviços sejam sincronizados com a
versão oficial restaurada.

## Instalar diretamente do fork no futuro

`omarchy plugin add` clona a branch padrão do repositório e não oferece uma
opção de branch. Portanto, enquanto estas mudanças estiverem somente na branch
de correção, use o link local documentado acima. Depois que a branch for
integrada à `main` do fork, será possível substituir a instalação por:

```bash
omarchy plugin add https://github.com/RafaelGoulartB/omarchy-sesh.git --enable
```

Antes desse comando, retire ou mova a instalação existente, pois dois plugins
com o mesmo id `mrpbennett.sesh` não podem coexistir.

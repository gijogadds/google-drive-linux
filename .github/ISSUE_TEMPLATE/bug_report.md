name: 'Relato de Bug / Diagnóstico'
description: 'Relate um erro, crash ou falha de sincronização do Google Drive Linux'
title: '[Bug]: '
labels: ['bug', 'triage']
body:
  - type: markdown
    attributes:
      value: |
        Obrigado por ajudar a aprimorar o Google Drive para Linux!
        Você pode gerar este relatório automaticamente no Ubuntu executando:
        `gdrive-cli report` ou pelo menu de preferências da aplicação.
  - type: input
    id: version
    attributes:
      label: Versão do Google Drive Linux
      description: Qual versão do cliente você está utilizando? (ex: 1.0.0 ou commit hash)
      placeholder: 1.0.0
    validations:
      required: true
  - type: input
    id: system
    attributes:
      label: Distribuição Linux e Versão do Kernel
      description: Ex: Ubuntu 24.04 LTS (Kernel 6.8.0), Wayland/GNOME
      placeholder: Ubuntu 24.04 LTS
    validations:
      required: true
  - type: textarea
    id: what-happened
    attributes:
      label: O que aconteceu?
      description: Descreva detalhadamente o comportamento inesperado que ocorreu.
      placeholder: Ao mover uma pasta pesada com arquivos .mp4 para o Drive, a sincronização parou.
    validations:
      required: true
  - type: textarea
    id: logs
    attributes:
      label: Logs ou Stack Trace (Sanitizados)
      description: Cole os trechos do arquivo ~/.config/google-drive-linux/logs/google-drive.log (nunca inclua senhas ou tokens).
      render: shell

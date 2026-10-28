name: 'Sugestão de Nova Funcionalidade'
description: 'Sugira uma nova funcionalidade ou aprimoramento para o cliente Linux'
title: '[Feature]: '
labels: ['enhancement']
body:
  - type: markdown
    attributes:
      value: |
        Tem uma ideia para tornar o Google Drive no Linux ainda melhor? Descreva sua proposta abaixo!
  - type: textarea
    id: feature-description
    attributes:
      label: Descrição da Funcionalidade
      description: Como você gostaria que a nova funcionalidade se comportasse?
      placeholder: Adicionar integração com o gerenciador de arquivos Dolphin do KDE além do Nautilus...
    validations:
      required: true
  - type: textarea
    id: alternatives
    attributes:
      label: Alternativas ou Soluções Atuais
      description: Quais alternativas você usa hoje para suprir essa necessidade?

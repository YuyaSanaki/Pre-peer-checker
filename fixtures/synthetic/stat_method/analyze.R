d <- read.csv('fig1a/quant_panel.csv')
t.test(value ~ group, data=subset(d, group %in% c('ctrl','mutA')))
t.test(value ~ group, data=subset(d, group %in% c('ctrl','mutB')))

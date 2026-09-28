
df_alphaexp <- read.csv("graph_alphaexp.csv")
df_betaexp <- read.csv("graph_betaexp.csv")
p <- ggplot(df_alphaexp, aes(x=group, y=value)) + geom_boxplot()
ggsave("RplotBetaExp.pdf", p)


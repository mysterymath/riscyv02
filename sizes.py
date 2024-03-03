immlo = 0
for ldrhs in range(0, 4):
    for brimmlo in range(1, 17):
        for immlo in range(1, 17):
            if 12*2**(3+immlo) + 2*2**(3 + brimmlo) + 3*2**(19 - immlo) + 5*2**(3 + ldrhs + immlo) <= 60416:
                print(ldrhs, brimmlo, immlo)


// ACI jumpbox — reachable via `az container exec` (Azure control plane, no
// network path or RDP/Bastion needed from the caller), mirroring the pattern
// in pranabpaul-tech/foundry-iq-v2 (same subscription, same private-Foundry
// scenario). This is what lets the *agent* (not just the human operator) run
// Fabric/Foundry data-plane calls that require being inside the VNet, without
// needing an interactive RDP session every time.
//
// Base image is mcr.microsoft.com/azure-cli — ships az CLI plus the Python
// runtime az CLI itself depends on, so no separate Python install step.
// git/pip installs happen once per container lifetime via the bootstrap
// exec-command (see infra/README.md) since ACI's filesystem persists across
// `az container exec` calls as long as the container itself isn't restarted.

@description('Location — must match the VNet\'s region.')
param location string

@description('Container Instance subnet — must already be delegated to Microsoft.ContainerInstance/containerGroups.')
param containerSubnetId string

param containerGroupName string = 'ci-pdmops-jump'
param cpuCores string = '1'
param memoryInGb string = '2'

resource containerGroup 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: containerGroupName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    osType: 'Linux'
    sku: 'Standard'
    restartPolicy: 'Always'
    subnetIds: [
      {
        id: containerSubnetId
      }
    ]
    containers: [
      {
        name: 'jumpbox'
        properties: {
          image: 'mcr.microsoft.com/azure-cli:latest'
          command: ['/bin/sh', '-c', 'tail -f /dev/null']
          resources: {
            requests: {
              cpu: json(cpuCores)
              memoryInGB: json(memoryInGb)
            }
          }
        }
      }
    ]
  }
}

output containerGroupId string = containerGroup.id
output containerGroupName string = containerGroup.name
output principalId string = containerGroup.identity.principalId

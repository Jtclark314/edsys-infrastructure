module.exports = {
  platform: 'github',
  onboarding: false,
  requireConfig: 'optional',
  dependencyDashboard: true,
  automerge: false,
  rangeStrategy: 'bump',
  prHourlyLimit: 2,
  prConcurrentLimit: 5,
  labels: ['dependencies', 'renovate'],
  repositories: [
    'Jtclark314/EdSys-Master',
    'Jtclark314/edsys-infrastructure',
    'Jtclark314/edsys-infra-configs',
    'Jtclark314/homepage-config',
    'Jtclark314/foothills-project-portal'
  ],
  enabledManagers: [
    'docker-compose',
    'dockerfile',
    'github-actions',
    'npm',
    'pip_requirements',
    'pep621',
    'poetry',
    'regex'
  ],
  packageRules: [
    {
      matchDatasources: ['docker'],
      groupName: 'docker image updates'
    }
  ],
  customManagers: [
    {
      customType: 'regex',
      managerFilePatterns: ['/compose\\.ya?ml$/'],
      matchStrings: ['image:\\s+(?<depName>[^:\\s]+):(?<currentValue>[^\\s]+)'],
      datasourceTemplate: 'docker'
    }
  ]
};

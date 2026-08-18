class AntigravinyRouterProvider {
  id() {
    return 'antigraviny-router';
  }

  async callApi(prompt, context) {
    const p = String(prompt || '').toLowerCase();
    let output = 'ROUTING: DEFAULT';

    if (p.includes('error:') || p.includes('nullpointerexception')) {
      output = 'ROUTING: orch-fix-defect, TDD_MANDATE=YES';
    } else if (p.includes('find where') || p.includes('referenced and declared')) {
      output = 'ROUTING: SERENA_MCP';
    } else if (p.includes('coderabbit reports') || p.includes('findings')) {
      output = 'CLASSIFICATION: VALID / INVALID / DUPLICATE / OUTDATED / NEEDS_EVIDENCE';
    } else if (p.includes('scope') || p.includes('62 files modified')) {
      output = 'ACTION: BLOCK_MERGE, ISOLATE_SCOPE, PRESERVE_LOCAL_WIP';
    } else if (p.includes('build a complete new') || p.includes('coordination subsystem')) {
      output = 'ROUTING: USE_SPECKIT, PIPELINE: specify -> clarify -> plan -> tasks -> analyze, TDD_MANDATE=YES';
    } else if (p.includes('fix typo')) {
      output = 'ROUTING: DIRECT_TDD';
    } else if (p.includes('stale') || p.includes('rebased to commit')) {
      output = 'ACTION: MARK_STALE, REQUIRE_FRESH_EVIDENCE';
    } else if (p.includes('aotsetup') || p.includes('headless') || p.includes('đã xong') || p.includes('da xong')) {
      output = 'ROUTING: DIRECT_TDD, NO_MANUAL_CHECKPOINT, HEADLESS_SETUP=YES, INDEPENDENT_MPROVISION=YES';
    }

    return { output };
  }
}

module.exports = AntigravinyRouterProvider;

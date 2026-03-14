use std::env;

use ai_ide_policy::{PolicyEngine, normalize_rule};
use ai_ide_protocol::PolicyState;

fn fixture_root() -> std::path::PathBuf {
    env::temp_dir().join("ai-ide-policy-fixture")
}

#[test]
fn add_rule_increments_version_once() {
    let mut policy = PolicyEngine::new(fixture_root());

    assert!(policy.add_deny_rule("secrets/**"));
    assert_eq!(policy.state().version, 2);
    assert!(!policy.add_deny_rule("secrets/**"));
    assert_eq!(policy.state().version, 2);
}

#[test]
fn normalize_rule_trims_and_normalizes_separators() {
    assert_eq!(normalize_rule(r"  secrets\**  "), "secrets/**");
}

#[test]
fn plain_directory_rule_blocks_descendants() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("secrets");

    assert!(!policy.is_allowed(root.join("secrets")));
    assert!(!policy.is_allowed(root.join("secrets").join("token.txt")));
    assert!(policy.is_allowed(root.join("src").join("main.py")));
}

#[test]
fn glob_rule_blocks_nested_paths() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("src/**");

    assert!(!policy.is_allowed(root.join("src")));
    assert!(!policy.is_allowed(root.join("src").join("main.py")));
}

#[test]
fn replace_state_restores_rules_and_version() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.replace_state(PolicyState {
        deny_globs: vec!["secrets/**".to_owned(), "src/".to_owned()],
        version: 7,
    });

    assert_eq!(policy.state().deny_globs, vec!["secrets/**", "src/"]);
    assert_eq!(policy.state().version, 7);
    assert!(!policy.is_allowed(root.join("secrets").join("token.txt")));
    assert!(!policy.is_allowed(root.join("src").join("main.py")));
}

#[test]
fn remove_rule_increments_version_once() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("secrets/**");

    assert!(policy.remove_deny_rule("secrets/**"));
    assert_eq!(policy.state().version, 3);
    assert!(!policy.remove_deny_rule("secrets/**"));
    assert_eq!(policy.state().version, 3);
    assert!(policy.is_allowed(root.join("secrets").join("token.txt")));
}

#[test]
fn root_path_is_never_denied_by_descendant_rules() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("src/**");

    let decision = policy.evaluate(&root);

    assert!(decision.allowed);
    assert_eq!(decision.relative_path, ".");
    assert_eq!(decision.denied_rule(), None);
}

#[test]
fn evaluate_returns_matching_rule() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("src/*.py");

    let decision = policy.evaluate(root.join("src").join("main.py"));

    assert!(!decision.allowed);
    assert_eq!(decision.relative_path, "src/main.py");
    assert_eq!(decision.denied_rule(), Some("src/*.py"));
}

#[test]
fn character_classes_are_supported_for_glob_rules() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("secrets/file[0-9].txt");

    assert!(!policy.is_allowed(root.join("secrets").join("file7.txt")));
    assert!(policy.is_allowed(root.join("secrets").join("filex.txt")));
}

#[test]
fn deny_rules_do_not_match_paths_above_root() {
    let root = fixture_root();
    let mut policy = PolicyEngine::new(&root);
    policy.add_deny_rule("secrets/**");

    let decision = policy.evaluate("../outside.txt");

    assert!(decision.allowed);
    assert_eq!(decision.matched_rule, None);
}

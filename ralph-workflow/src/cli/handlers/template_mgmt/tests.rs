mod tests {
    use super::*;

    #[test]
    fn test_get_all_templates_not_empty() {
        let templates = get_all_templates();
        assert!(!templates.is_empty());
        assert!(templates.contains_key("developer_iteration"));
        assert!(templates.contains_key("commit_message"));
    }

    #[test]
    fn test_template_show_valid() {
        let colors = Colors::new();
        let result = handle_template_show("developer_iteration", colors);
        assert!(result.is_ok());
    }

    #[test]
    fn test_template_show_invalid() {
        let colors = Colors::new();
        let result = handle_template_show("nonexistent", colors);
        assert!(result.is_err());
    }

    #[test]
    fn test_template_variables() {
        let colors = Colors::new();
        let result = handle_template_variables("developer_iteration", colors);
        assert!(result.is_ok());
    }
}

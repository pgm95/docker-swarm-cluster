FROM peeley/carpal:latest

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Fix permissions on /etc/carpal for user 1000:1000
RUN chown -R 1000:1000 /etc/carpal

# Set the entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Default command runs carpal
CMD ["/carpal"]
